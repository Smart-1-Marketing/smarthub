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

    # ---------------- Three bubbles that explained nothing ----------------
    # Each of these keys was placed on a tool's own title and had no entry
    # here, so hub-help.js removed the dot client-side: the template read as
    # helped, the screen showed nothing, and nothing anywhere reported it.
    # Video Search's template even carries a comment saying its key must not
    # be renamed *because renaming would orphan the bubble* -- protecting a
    # key that pointed at nothing. hub/help_audit.py is the check now, and
    # /api/integrity runs it.
    _h("site_blocks.intro", "What this builds, and where it goes",
       "A landing-page section for smart1marketing.com, in the same visual "
       "language as the industry pages \u2014 Montserrat, navy, the accent "
       "pair, the 1140px column. Fill in the copy, pick a theme, and paste "
       "the HTML into a Custom HTML element on the site. Every block carries "
       "its own scoped stylesheet, so nothing it defines can reach the rest "
       "of the page \u2014 which is what makes it safe to paste into a page "
       "somebody else built."),
    _h("client_health.report", "What is outstanding, per client",
       "Everything on this page is read from the systems that already hold it "
       "\u2014 the insertion orders in Knack, the creative audit, the proposal "
       "store, the review rounds and the last website reading. Nothing is "
       "typed in, so nothing here can go stale by being forgotten. Clients "
       "are ordered by how much is outstanding rather than by name, because "
       "the question is which one needs an hour today. A source that could "
       "not be read is named at the top: anything it would have raised is "
       "missing from every row rather than absent from it, and a quiet page "
       "over a failed read is the one answer this report must never give."),
    _h("client_health.marks", "Ignore, Done, and what happens next",
       "Ignoring an issue says it is not something we are going to act on; "
       "Done says it has been dealt with. Neither deletes anything \u2014 both "
       "move the row into its own list under the client with your name and "
       "the date on it, and one press puts it back. A Done mark is about the "
       "issue as it stood: if what it says changes, the mark is reported as "
       "superseded and the row is open again, because \u201cnobody has looked\u201d "
       "and \u201csomebody looked at a different ask\u201d are different situations."),
    _h("client_owner.assign", "Who owns which client",
       "An assignment is a Hub overlay and is never written to the client "
       "record \u2014 taking one off leaves everything exactly as it was. What it "
       "changes is My Clients, which is where the person you assign sees the "
       "work. A media partner can be a **standing rule**: whatever that "
       "partner carries belongs to the person named, including clients they "
       "gain later. Nothing is written per client, so the rule follows the "
       "book as it changes and clearing it needs no undoing row by row. A "
       "client assigned by hand keeps that owner, one taken off everybody "
       "stays off, and a client two partners\u2019 rules disagree about is left "
       "unassigned and named rather than given to one of them \u2014 every row "
       "says which of those decided it."),
    _h("social.planner", "A month of posts in one pass",
       "Builds a client's organic month from what the Hub already knows about "
       "them, so the calendar starts full rather than empty. It stops at a "
       "CSV for Smart 1 Suite's bulk upload on purpose: posting straight to "
       "Suite needs a scope HighLevel has not granted yet, and ending at the "
       "export means the drafting earns its keep either way. The copy checks "
       "are code rather than a note in the prompt \u2014 a price, a "
       "percentage, a phone number or a deadline that nobody typed blocks "
       "the plan, because a month of posts is bulk work that gets skimmed."),
    _h("video_backgrounds.overview", "Searching footage by what is on screen",
       "Every clip in the two folders we own is described by a vision pass, "
       "so you can search for what is actually in the shot rather than for "
       "whatever the file was called. What comes back is a URL already sized, "
       "muted and trimmed to sit behind a headline. Read the library status "
       "above the results first: an empty list means Cloudinary is unset, or "
       "the sweep has not reached those clips yet, or there is genuinely no "
       "match \u2014 three different answers that would otherwise look "
       "identical."),

    _h("video_tools.dead_air", "It reads the level, not the words",
       "Every other silence cutter transcribes the audio and cuts the gaps "
       "between words, which is wrong on our work: a music bed, a sound "
       "effect and a held beat all read as silence to a transcript, and all "
       "of them get removed. This one reads the audio\u2019s actual level \u2014 "
       "from a waveform image Cloudinary draws, which is also the picture "
       "shown on the page \u2014 so sound under a montage counts as sound. "
       "Two controls decide the result: the gap is what counts as dead air, "
       "and the air left behind at each cut is what decides whether the "
       "output sounds edited or just faster. Look at the cut list before you "
       "render; that is the step where a bad cut is free to undo."),
    _h("video_tools.reframe", "What a crop costs, said out loud",
       "A 16:9 frame cropped to 9:16 keeps under a third of its width, and "
       "automatic framing keeps ONE subject rather than all of them \u2014 a "
       "second person, a product on the table, a lower third or a corner "
       "logo goes, and the preview is where you find that out. The page "
       "reports both numbers rather than only the flattering one. The "
       "alternative mode pads the frame against a blurred blow-up of itself "
       "and loses nothing, which is right for a wide shot and weak for "
       "anything already tight. Either way this is a cutdown of a landscape "
       "master, not a spot built vertical: sound-off legibility and a "
       "two-second hook are decisions, not a crop."),

    # ---------------- Social content requests ----------------
    # This screen shipped with no explanation on it at all, which is exactly
    # how Smart 1 Ads shipped: hub/help.py, hub_help.js and the tour machinery
    # all working, and nothing on the page opting into any of it. Every key
    # below is placed by modules/social_planner/templates/staff_requests.html;
    # test_social_content.py asserts the two stay in step, because a bubble
    # whose key is not in this registry is removed client-side and reads as
    # helped from the template while showing nothing on the page.
    _h("social.requests.queue", "One queue, every location",
       "A client account is one social presence and often several shops. "
       "Everybody at the client opens the same link, says which location they "
       "are, and their request lands here — instead of in whichever inbox "
       "they happened to know. The location is how you sort it, not where it "
       "gets posted: one shared page is what the client has."),
    _h("social.requests.overdue", "Overdue, and what it is not",
       "The day the client asked for has passed and the request still is not "
       "scheduled. A request that said \u201cas soon as you can\u201d is never "
       "overdue — it named no day, and treating that as today would turn "
       "every one of them red by tomorrow. Declined and duplicate requests "
       "drop out too: they have been answered."),
    _h("social.requests.duplicate", "Possible duplicate",
       "Another open request from this same client wants something live in "
       "an overlapping window. It is a flag and nothing else — as often two "
       "locations legitimately both wanting that week as it is one ask sent "
       "twice, and nothing here can tell them apart. Nothing is merged or "
       "declined automatically; you confirm it or clear it. It never pairs "
       "across clients: two businesses wanting the same Friday is a Friday."),
    _h("social.requests.link", "Their link",
       "One signed link per client account, not one per location — a link "
       "each would be the inbox-per-shop arrangement this replaces. It is "
       "derived from the client's name rather than stored, so it is the same "
       "string every time and there is nothing to lose. Turning it off stops "
       "all four of their pages at once, including any copy already pasted "
       "on their intranet."),
    _h("social.requests.turnaround", "The turnaround line",
       "What the client's confirmation screen is allowed to promise. It is "
       "measured from requests actually triaged, and until there are a few "
       "it says so rather than quoting a number nobody has checked — a "
       "figure invented here is a commitment made on the client's behalf."),
    _h("social.requests.promote", "Promote into a plan",
       "Turns the request into a slot on that client's month, carrying its "
       "copy, its photographs and its date across, and joins the two so the "
       "post says which request it came from. It will not invent a plan: "
       "with no month built it refuses and names the step, because the "
       "channels and the post mix belong to the plan rather than to one "
       "request. What the client typed also becomes authorized text, so "
       "their own offer is not flagged as invented."),
    _h("social.requests.ideas", "Ideas and the weighting",
       "One-line ideas the client swipes Like or Pass on. The weighting is "
       "liked \u00f7 (liked + passed + 1), which you can check against the two "
       "counts beside it, and it only ever decides which kinds of post get "
       "offered next — never the words of anything. A share of each batch "
       "goes to kinds nobody has answered on, or the mix converges on "
       "whatever they liked first and stops learning."),
    _h("social.requests.suite", "Pushing to Smart 1 Suite",
       "Posting from here needs the socialplanner/post.write scope on the "
       "Suite app, and HighLevel grants what it recognizes at consent "
       "without saying anything about the rest — so it is checked before a "
       "push rather than discovered by one. Until the agency re-consents, "
       "the CSV export loads the same plan into Social Planner's Bulk "
       "Upload; the line here says which of the two you are being offered."),


    # ---------------- Proposal Builder: the reach panel ----------------
    # Four numbers sat above a target area with nothing saying what any of
    # them counted, so "addressable audience" got read as "people who will
    # see the ad" -- which it is not, by an order of magnitude. Each column
    # now says what it is, and where the number came from.
    _h("sales_builder.areas.pop", "Estimated population",
       "Everyone living inside the target areas, before any targeting is "
       "applied. For a radius it is the area of the circle times an assumed "
       "density that falls as the radius grows — a 10-mile ring drawn on a "
       "city center is far denser than a 50-mile one reaching into farmland. "
       "Overlapping areas are added together, so two rings five miles apart "
       "count their shared households twice. It is an estimate, and the AI "
       "re-estimate sizes each area for real."),
    _h("sales_builder.areas.aud", "Addressable audience",
       "The share of that population the campaign could target — population "
       "narrowed to adults, then to the age ranges and household incomes "
       "chosen on this step. It is who we are allowed to bid on, not who "
       "will see an ad: how many of them actually get reached depends on the "
       "budget and the CPMs in the media plan."),
    _h("sales_builder.areas.hh", "Households",
       "The addressable audience expressed as homes rather than people, at "
       "roughly 1.9 adults per household. Useful for anything bought or "
       "decided per address — Connected TV, direct mail, IP targeting — "
       "where two people in one house are one impression, not two."),
    _h("sales_builder.areas.dev", "Devices",
       "Roughly how many screens that audience is reachable on — phone, "
       "laptop, tablet, connected TV — at about 2.3 per person. It is why "
       "frequency is counted per person rather than per device: the same "
       "someone can be served the same ad on all of them."),

    # ------------- The wizard's fourteen steps -------------
    # Written where CLAUDE.md already documents a trap, because those are the
    # places a rep gets it wrong and the copy can say what the field does to
    # the output rather than what it is. Placed on the step heading by
    # renderStep(), which is the one function that draws all fourteen.
    _h("sales_builder.areas.list", "A ZIP exception is a rule, not a note",
       "A radius does not stop at a state line and a campaign frequently "
       "does \u2014 a client licensed in one state, a franchise with a protected "
       "territory, a dealer whose registration works one side of the river. "
       "Write it the way you would say it (\u201conly New Jersey zip codes\u201d, "
       "\u201ceverything except Ohio\u201d) and it narrows the list that reaches "
       "both the proposal and the insertion order, so the two cannot "
       "disagree about where the campaign runs. A rule that could not be "
       "read says *not applied* beside the sentence rather than quietly "
       "doing nothing, and filtering only ever removes: if it leaves "
       "nothing, that is reported as nothing rather than falling back to "
       "the unfiltered list."),

    _h("sales_builder.goal.pick", "What the goals decide",
       "The goals choose which products get recommended on the media mix "
       "step and which KPIs the campaign is judged on later. They are a "
       "starting point, not a lock: you can add or remove any product "
       "afterwards. What they will not do is pick a filler product — run of "
       "network is untargeted inventory that tops a buy up, so it is "
       "addable by name and is never recommended for anybody."),

    _h("sales_builder.customer.audit", "Read the audit, or run a new one",
       "Reading what we already measured about their site is free and "
       "instant. Running a new audit spends a scan credit, so it asks first. "
       "Past sixty days the reading is about a site that may have been "
       "rebuilt since — it still fills the proposal in, with the date shown, "
       "because an old answer with a date on it beats leaving a rep with "
       "nothing while they wait."),

    _h("sales_builder.landing.url", "The page is fetched, not described",
       "Smart 1 requests the page and counts its conversion points off the "
       "markup — phone links, forms and their field counts, booking tools "
       "and chat widgets by their own script signatures. The model is given "
       "those facts and asked only for judgment, so a recommendation is "
       "about the page as it actually is. A page that could not be fetched "
       "is reported as not measured and is never reviewed anyway."),

    _h("sales_builder.marketing.answers", "Every answer changes the document",
       "These feed the We Suggest They Should list, the friction section, "
       "and what the Suite license is shown to close. Leaving one blank is "
       "not the same as answering no: an unanswered question is left off "
       "the client's document rather than printed as a confident No, so "
       "skip what you do not know rather than guessing."),

    _h("sales_builder.guardrails.exclusions", "Where these end up",
       "Exclusions, negative keywords and restricted audiences are written "
       "onto the insertion order's exclusions field, so what you put here "
       "reaches whoever traffics the campaign. Removing a negative later "
       "always counts as a material edit — it reopens spend the list "
       "existed to stop — and sends the estimate back through the check."),

    _h("sales_builder.measurement.kpi", "Set this before the products",
       "What counts as success decides which products belong on the plan, "
       "which is why it is asked first. It also drives Expected Results & "
       "ROI, which is a KPI framework rather than an impression count: each "
       "product is shown with the range that inventory normally delivers, "
       "said once in the client's own words as an expectation and not a "
       "guarantee."),

    _h("sales_builder.audience.competitors", "Name them, do not tick them",
       "A ticked box saying \u201ccompetitor conquesting\u201d is not a campaign — "
       "whoever builds the geo-fence still has to ask who. The client is the "
       "only person in the room who knows who they lose business to, so ask "
       "on the call. A row with no address is still worth keeping: "
       "conquesting by brand and browsing behavior needs no location, and "
       "nothing here is ever guessed from a name."),

    _h("sales_builder.budget.working", "The plan is the number",
       "This is what the client asked for, and it is kept. Once there are "
       "line items, they are what is being bought — so the cover, the media "
       "plan, the investment summary and the insertion order all derive "
       "from the plan rather than from this figure. If the two have parted "
       "company the step says so, because a rep who set one number and "
       "built a different plan should not have to wonder which won."),

    _h("sales_builder.mix.rates", "The card rate is what we pay",
       "Every rate on the Smart 1 card is the buy-side number. CPM and CPV "
       "lines are quoted to the client at twice it by default, editable per "
       "line, which is where the margin lives — quote one straight through "
       "and the delivery table promises impressions the budget cannot buy. "
       "A management fee, flat fee or custom quote has nothing to multiply "
       "and is left exactly as the card lists it."),

    _h("sales_builder.creative.gate", "Who is producing the files",
       "A campaign that reaches an insertion order with no spot or no "
       "banners behind it is a launch date nobody can hit, so each medium "
       "on the plan is asked about here. If Smart 1 is comping production "
       "on a buy too small to pay for it, that gets one explicit "
       "confirmation with the number shown — and the confirmation lapses if "
       "the budget is later cut below what was confirmed."),

    _h("sales_builder.packages.investment", "Three kinds of money, kept apart",
       "Recurring platform licensing, media spend and one-time production "
       "are never added together, so a client can tell what stops if they "
       "pause the campaign and what does not. The Suite license is a "
       "separate product with its own line and can be left off the quote "
       "entirely; adjusting its price records the reason internally, "
       "because a discount nobody wrote down is one nobody can renew."),

    _h("sales_builder.document.sections", "Editing what the client reads",
       "Every section can be rewritten, hidden or left to generate, and the "
       "tables under them can be edited or excluded — useful for a location "
       "under NDA or a KPI they asked us to drop. An edited table is drawn "
       "in amber and stops recomputing, which is exactly true: it will not "
       "follow a later change to the budget or the plan. Three sections "
       "cannot be removed, because the document is quoted from them."),

    _h("sales_builder.review.deliver", "Sending it, and how long it stands",
       "The client link shows the same document you are looking at and the "
       "one thing on it is accept. How long the price stands is set here "
       "and runs from when it was sent rather than written; past that the "
       "client is told so above the document and given somebody to ask, "
       "rather than reading four pages and finding out at the end. An "
       "acceptance is tied to one revision, so revising an accepted quote "
       "does not carry their yes onto a document they never saw."),

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
       "Everything the SEO Image Pipeline has optimized for this client, plus "
       "any logo on their brand record. Each tile downloads or deletes on the "
       "spot — deleting removes the file from the client gallery and from the "
       "pipeline's archive, and cannot be undone. The count is this client's, "
       "not the whole archive's.", step=4,
       selector="[data-tour='client-images']"),
    _h("hub.client360.spend", "What they are already spending",
       "The first thing worth knowing about a client, and the one that decides "
       "what the next conversation is about. Every figure is a third-party "
       "estimate of somebody else's spend, not a billed number: the total "
       "covers only what carries a figure, and what is deliberately left out "
       "of it is named beside it — Meta publishes the ads and never the money, "
       "so counting paid social as zero would understate a business by "
       "thousands in a clean confident row. Annualising is our multiplication "
       "and a cost per visit is their own two numbers divided, so both say so. "
       "Underneath it is what the audit says we could fix, which is the "
       "finding rather than a product name. This is the same reading of the "
       "same audit the Website Audit tool and a prospect record show — read "
       "again with the date on it, and nothing here is fetched afresh or "
       "costs anything."),
    _h("hub.client360.scanfacts", "What we know about this business",
       "About 440 things are read off a client's website and this record used "
       "to show four of them. This is the rest of what is worth reading: the "
       "Google Business Profile, review standing, social accounts, what they "
       "are already spending on ads, whether a pixel is even on the site, and "
       "the registrar. Nothing here is fetched afresh or costs anything — it "
       "is what was last read, read again, with the date on it. A row that is "
       "absent was not measured; it is never shown as a zero. The name, "
       "address and phone number here are offered into the client info strip "
       "at the top of this record, where one press keeps them."),

    # ---------------- Website Audit ----------------
    _h("hub.website_audit.intro", "What this tool is for",
       "One audit of a website already knows what the business is spending on "
       "Google Ads, whether their Google listing is claimed, how many reviews "
       "they have, whether a pixel is on the site at all and about seventy "
       "other things. This is that audit, read in the order a sales "
       "conversation happens: what they are already spending first, then what "
       "is worth fixing, then everything else. Reading it costs nothing and "
       "spends no credit. Running a new one does both, so it asks first — and "
       "it offers to, unprompted, once a reading is over sixty days old, "
       "because a proposal written from an older one describes a site that "
       "may have been rebuilt since."),
    _h("hub.website_audit.intake", "What they told us",
       "The handful of answers a crawler cannot get at: what they sell, where "
       "their customers come from, what they are already spending. These are "
       "kept apart from everything observed and never merged into it — where "
       "the two disagree the disagreement is the finding, and folding one into "
       "the other destroys the only evidence of it. Every question here "
       "changes something in the proposal downstream, and each one says what."),

    _h("hub.website_audit.ask", "Reading one and running one",
       "Reading the last audit costs nothing and spends no credit — it is a "
       "reading somebody already paid for, and the date it was taken is "
       "printed on it. Running a new one spends a credit and takes minutes, "
       "so it confirms first. A client is searched rather than typed: a name "
       "that matches nothing is refused rather than filed against a client "
       "nothing joins to.", step=2, selector="#askCard"),
    _h("hub.website_audit.spend", "What they are already spending",
       "This leads, because a business putting $2,400 a month into Google Ads "
       "is a different sale from one putting in nothing. The total covers "
       "only what carries a number — Meta publishes the ads and never the "
       "spend — and whatever was left out of it is named beside it in words, "
       "or a five-figure understatement gets quoted confidently. The "
       "arithmetic says which two figures it divided and whose they were.",
       step=3, selector=".card.spend"),
    _h("hub.website_audit.opportunities", "What we could fix",
       "Each row is what was measured and what it costs them, with the "
       "product as the consequence rather than the headline: the finding is "
       "the half that survives being read out to the client, and the product "
       "is what a rep gets argued with over. A check the plan did not run "
       "raises nothing at all — absent is never a clean bill.",
       step=4, selector="[data-block='opportunities']"),
    _h("hub.website_audit.lead", "Every audit is a lead",
       "Somebody typed a business and a website into this Hub, which makes "
       "them a prospect whatever else they are. The row goes to the one lead "
       "store and on to Smart 1 Suite, and the two writes are reported apart "
       "— saved here and created there are different outcomes. A lead with "
       "neither an email nor a phone number is refused by name rather than "
       "created: a contactless lead reads as a live prospect on every count "
       "that follows. Once it is filed the prospect record is one click away, "
       "and that is where the audit, the proposal and the files live "
       "together.", step=5, selector="#leadCard"),
    _h("hub.website_audit.proposal", "Turning it into a proposal",
       "The builder opens with the discovery answers, the target areas and "
       "the findings already in it, so the quote is written from what was "
       "measured rather than from memory. A reading over sixty days old says "
       "so first: a proposal written from an older one describes a site that "
       "may have been rebuilt since.", step=6, selector="#nextCard"),

    # ---------------- Prospect record ----------------
    _h("hub.prospect.intro", "What this record is for",
       "Everything the Hub knows about one prospect, in one place: what they "
       "are spending, what the audit found, what has been quoted, the files "
       "collected for them and what has happened. Smart 1 Suite owns the "
       "working state and the Hub owns the evidence — the stage, the owner "
       "and the conversation are in the CRM, which is where the calls and the "
       "texts already are, and a stage stored here as well would be two "
       "systems answering one question with nothing saying which to believe. "
       "So this record reads the stage and never writes one.",
       step=1, selector=".strip"),
    _h("hub.prospect.spend", "What they are already spending",
       "The same reading the Website Audit tool shows, and deliberately not a "
       "second description of it — two screens describing a business's own "
       "money differently is how a reader learns to believe neither. What was "
       "left out of the total is named rather than counted as nothing.",
       step=2, selector="[data-card='spend']"),
    _h("hub.prospect.suite", "Where this has got to",
       "Read from Smart 1 Suite, never decided here. Four kinds of empty, "
       "and only one of them means chase this: the Suite is not configured, "
       "the lead never reached it, the Suite refused the read, or the Suite "
       "answered fine and no deal is open. The first three say so; the fourth "
       "is the one that means somebody should open a deal. A note typed here "
       "is posted to the Suite contact, so it lands where the next person to "
       "pick this up will look, rather than in a second notebook.",
       step=3, selector="[data-card='suite']"),
    _h("hub.prospect.audit", "What the audit found",
       "The same audit the tool and the client record read. Over sixty days "
       "old it says so and offers the rescan, because a quote written from an "
       "older reading describes a site that may have been rebuilt since. "
       "Running one spends a credit and takes minutes, so the row does not "
       "vanish on success — it would be claiming a result that does not exist "
       "yet.", step=4, selector="[data-card='audit']"),
    _h("hub.prospect.proposals", "What has been quoted",
       "Proposals filed against this business. Audited and nothing quoted "
       "is the band the whole audit pipeline exists to fill, and it is the "
       "third thing the prospect queue sorts on — so Start a proposal at the "
       "top of this record opens the builder with the audit already read into "
       "it.", step=5, selector="[data-card='proposals']"),
    _h("hub.prospect.assets", "Files on this prospect",
       "The mock-up they were sent, the screenshot of the competitor they "
       "complained about, the rate sheet they emailed over — the things that "
       "otherwise live in somebody's inbox. They sit in a folder of their "
       "own rather than a client gallery, because a prospect has no client "
       "key yet and filing them together is how one company's assets land on "
       "another's record. Deleting reports the record row and the stored copy "
       "apart: one tick covering both is how somebody learns not to trust the "
       "tick. Converting re-files them under the client by naming them, not "
       "by uploading a second copy.", step=6, selector="[data-card='assets']"),
    _h("hub.prospect.duplicates", "The same prospect, twice",
       "The same business reaches the panel more than once and always will — "
       "the widget on a client's site in March, an audit in May, a landing "
       "page in between. Rows grouped on the same email address or the same "
       "website are near enough proof; an exact company name on its own is "
       "worth an eyeball and nothing more, since two franchises of one brand "
       "carry one name and are two businesses with two owners. Merging is "
       "done on the Leads panel and not here, because it is a decision made "
       "with every row on screen: which one survives keeps its own details "
       "and fills its blanks from the rest.",
       step=7, selector="[data-card='duplicates']"),
    _h("hub.prospect.timeline", "Everything that has happened",
       "Assembled from the sections that were actually measured, and the ones "
       "that were not are named on it rather than shortening it in silence — "
       "a history missing exactly the fortnight somebody is asking about, "
       "with nothing saying so, is worse than no history.",
       step=8, selector="[data-card='timeline']"),
    _h("hub.prospect.convert", "Convert to a client",
       "A link, never a creation. A client in this Hub is a business with "
       "a product in Knack — that is what billing reads — so this refuses a "
       "name the registry does not know rather than inventing an account the "
       "Hub shows and no invoice ever mentions. What it adds is the "
       "carry-across: the files are re-filed under the client, and the "
       "history of who came in and from which tool survives."),

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
       "and the brand colors, and the colors drop straight into your picker.",
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
       "That's the whole reason this tool normalizes values for you.", step=2,
       selector="[data-tour='utm-medium']"),
    _h("utm.form.campaign", "Name it so you'll recognize it in six months",
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
       "The downloaded HTML reads the fonts and colors off the page it came "
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
    _h("hub.status.presence", "What \u201csigned in now\u201d can and cannot mean",
       "The Hub keeps no session table \u2014 signing in issues a signed "
       "cookie and nothing is ever told that somebody has left, so closing a "
       "laptop and reading a long page look identical from here. This is "
       "everybody seen in the last fifteen minutes, which is a different "
       "claim from who is at their desk. The page each person was on is "
       "deliberately not recorded.",
       step=1, selector="[data-tour='status-presence']"),
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

    # The webmaster dashboard had no bubble and no tour on it at all: 450
    # lines of sortable table whose every number is drawn from a fetch, and
    # three different reasons a cell can be blank. The three below are the
    # questions the page cannot answer about itself.
    _h("hub.webmaster.roster", "Who is on this list",
       "Every client with a live SEO product, the same roster the SEO client "
       "list is built from \u2014 so a client missing here is missing there "
       "too, and the cause is the product, not this page.",
       step=1, selector="[data-tour='wm-roster']"),
    _h("hub.webmaster.property", "Why a row has no numbers",
       "Traffic comes from the Analytics property attached to that client. "
       "No property attached, Google refused the read, and still fetching are "
       "three different blanks and the row says which \u2014 none of them "
       "means the client had no visitors.",
       step=2, selector="[data-tour='wm-numbers']"),
    _h("hub.webmaster.attach", "Attaching a property",
       "Attaching here records it against the client, so the next sweep and "
       "Client 360 both see it. A property that disagrees with the one on "
       "the website record is left alone rather than overwritten \u2014 that "
       "disagreement is the finding.",
       step=3, selector="[data-tour='wm-attach']"),

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

    # ---------------- Smart 1 Ads ----------------
    # The module had no help at all: no bubbles, no tour, and a walkthrough
    # pointing at fields three of which no longer existed. What follows is the
    # explainer for each staff screen. The client-facing estimate at
    # /tools/ads/estimate/<token> deliberately has none of it — it is chrome-free
    # for a prospect, and staff notes have no business in a document a client
    # reads.
    _h("ads_builder.generator.client", "Look the client up first",
       "Matched on domain, then on an exact name — never a substring, so "
       "\u201cRiverside HVAC\u201d will not collect \u201cRiverside HVAC Supply\u201d. "
       "Picking the client is what files the finished proposal onto their 360 "
       "record; a name typed free-hand builds the same campaign and leaves the "
       "client\u2019s record showing nothing was ever quoted. A business that is "
       "genuinely new goes to Smart 1 Suite as a lead instead — nothing is "
       "written to Knack.", step=1, selector="[data-tour='ads-client']"),
    _h("ads_builder.generator.newclient", "A lead needs a way to reach them",
       "An email or a phone number, one of the two. A contact with neither "
       "reads as a live prospect on every count that follows and can be chased "
       "by nobody, so the lead is refused by name rather than created. The "
       "campaign is still built and still filed under the business name and "
       "website, so it joins the client record the day that record exists."),
    _h("ads_builder.generator.landing", "The landing page is read, not described",
       "This page is fetched and its conversion points counted off the markup — "
       "phone links, forms and their field counts, booking tools and chat "
       "widgets by their own script signatures, CTA buttons. Every finding "
       "carries the evidence, so the estimate can say \u201cthe number on the page "
       "is (317) 555-0142\u201d rather than \u201cthis page has a phone number\u201d. "
       "Point it at the page the click actually lands on: a page that could not "
       "be fetched is reported as not measured, and the model is then told not "
       "to describe the page at all.",
       step=2, selector="[data-tour='ads-landing']"),
    _h("ads_builder.generator.sector", "Sector sets the benchmark and the negatives",
       "It picks the average CPC band every click estimate on the estimate is "
       "computed from — an industry benchmark for the sector, not a measured "
       "cost for this account — and it seeds the negative keyword themes. For a "
       "local service business the negative list is what stops you paying for "
       "\u201chvac jobs\u201d and \u201chvac school\u201d, and it matters more than the "
       "positive one."),
    _h("ads_builder.generator.audience", "B2B and B2C search differently",
       "The answer is handed to the model as an instruction, not a label. B2B "
       "keeps consumer and DIY intent out of the keyword set; B2C keeps "
       "wholesale and trade-account intent out. \u201cBoth\u201d builds them as "
       "separate ad groups — one blended group serves consumer copy to a "
       "purchasing manager and commercial copy to a homeowner, and neither "
       "converts."),
    _h("ads_builder.generator.goals", "Each goal changes the campaign",
       "These are structural, not a wish list. Calls bring call assets and ad "
       "scheduling matched to when somebody answers; appointment bookings need "
       "a live booking tool on the page; chat needs a widget staffed in the "
       "hours the ads run. The landing-page read is checked against what you "
       "tick, and a goal the page cannot do is reported — bidding for bookings "
       "against a page with no booking tool spends the budget and books nobody.",
       step=4, selector="[data-tour='ads-goals']"),
    _h("ads_builder.generator.areas", "One campaign, several areas",
       "A dealer group with four rooftops is one campaign in four places. Typed "
       "into a single box the reach estimate sizes them as one, so each area is "
       "its own row and the sizing is done on the server — the same helper the "
       "Proposal Builder uses, so a campaign and its proposal cannot disagree "
       "about how big the audience is. The label and the reach under each row "
       "are redrawn without touching what you are typing.",
       step=3, selector="[data-tour='ads-areas']"),
    _h("ads_builder.generator.donottarget", "This is an instruction, not a preference",
       "Whatever goes here is written into the negative keywords and kept out "
       "of the positive ones — a service they are dropping, a town they do not "
       "cover, commercial work they do not want. Removing one of those "
       "negatives later counts as a material edit however small it looks, "
       "because it reopens spend this list existed to stop."),
    _h("ads_builder.generator.seasonal", "\u201cNot asked\u201d is not \u201cno\u201d",
       "Every yes/no here is tri-state and starts at not asked. An unanswered "
       "question is left off the client\u2019s estimate entirely rather than "
       "printed as a confident No — which would be us telling a client "
       "something they never said."),
    _h("ads_builder.generator.budget", "A budget is optional, and the tiers are sized either way",
       "Most first conversations have no number in them, so leave it unset and "
       "the model sizes Good / Better / Best and costs the campaign at the tier "
       "it recommends — the estimate then says in as many words that no budget "
       "was given. With a number, you get the same three tiers, which is how "
       "you show a client what the next step up buys. Each tier\u2019s click "
       "estimate is recomputed here from the sector CPC rather than taken from "
       "the model, because that is the number a client checks the tier against.",
       step=5, selector="[data-tour='ads-budget']"),
    _h("ads_builder.generator.build", "What Generate actually does",
       "Thirty to sixty seconds, and four distinct jobs: read the landing page, "
       "plan the campaign, write the keywords and size the budget — the stages "
       "tick off as they happen so a slow one is visible. Nothing is sent to "
       "Google here and nothing spends: this produces a draft proposal and an "
       "estimate for you to read, edit and approve.",
       step=6, selector="[data-tour='ads-generate']"),

    _h("ads_builder.proposal.details", "Editing clears the approval",
       "Approving is a statement about one specific document, so any edit marks "
       "the approval superseded. A material change — the budget, the audience, "
       "the do-not-target list, a removed keyword, a removed negative — sends "
       "the estimate back through the AI review before it can be approved "
       "again. That is two presses on purpose: the first press returns the "
       "re-check, so a budget quartered by a typo shows you what it did to the "
       "plan before the document a client reads is signed off.",
       step=1, selector="[data-tour='ads-details']"),
    _h("ads_builder.proposal.budget", "Changing the budget re-runs the review",
       "The tiers, the click estimates and the viability verdict are all read "
       "off this number, and the estimate a client reads quotes it. Changing it "
       "is material by definition, so the approval goes with it."),
    _h("ads_builder.proposal.logo", "Where the logo came from, not who supplied it",
       "The brand data already stored against the client is tried first, then a "
       "lookup behind a button because that one is billed, then your own "
       "upload. Each answer says which of the three it was. Nothing is guessed "
       "at — no favicon scraped off the landing page, no invented "
       "/logo.png — because a wrong logo on a client-facing estimate is worse "
       "than none: nobody proof-reads the thing they recognize."),
    _h("ads_builder.proposal.landing", "Measured facts, and judgment, kept apart",
       "The left of this panel is what was read off the page with the evidence "
       "beside it; the model is given those facts and asked only for judgment. "
       "A page that could not be fetched says not measured — never zero, which "
       "would read as a page with nothing on it. The finding worth acting on is "
       "a conversion action the client asked for that the page cannot do.",
       step=2, selector="[data-tour='ads-page']"),
    _h("ads_builder.proposal.competitors", "A researched name is a suggestion until you tick it",
       "These arrive unaccepted and only ticked names reach the client\u2019s "
       "estimate. Printing the lot is us telling a client who their competitors "
       "are on the model\u2019s say-so, and it is the paragraph a client checks "
       "hardest.", step=3, selector="[data-tour='ads-competitors']"),
    _h("ads_builder.proposal.workshop", "Working notes, and which source wrote them",
       "Three drafts that never reach the client estimate: ad copy variations, "
       "extension ideas read off the landing page's own fetched copy, and SEM "
       "quote help. The last one names its source, because the answers are "
       "different confidences — the Pickaxe assistant works from its own "
       "Google Ads benchmark library, and the Hub's fallback is general "
       "knowledge that deliberately quotes no cost figures, since a number "
       "with no provenance beside the labeled benchmark would be a fourth "
       "kind of CPC. Extension ideas refuse rather than guess when the page "
       "cannot be read: the fetched text is the analysis input, and an "
       "analysis of an unread page is the model reviewing its own guess."),
    _h("ads_builder.proposal.keywords", "Nothing is removed until you apply it",
       "Click keywords to mark them, then Remove selected. Cutting a keyword is "
       "material, so it clears the approval and re-runs the review — read the "
       "match types while you are here: exact, phrase and broad are three "
       "different spend profiles on the same words.",
       step=4, selector="[data-tour='ads-keywords']"),
    _h("ads_builder.proposal.negatives", "Removing a negative is always material",
       "However small it looks. Every term in this vault is spend that will not "
       "happen; taking one out reopens it, so it goes back through the review "
       "like a budget change. Adding is cheap — type them in as the client "
       "names things they do not want."),
    _h("ads_builder.proposal.approve", "Approve before you send anything",
       "The client link route refuses an unapproved estimate outright, so this "
       "press is the gate on the whole client-facing half of the tool. It "
       "records who approved what and when, and the version approved is the "
       "version the link shows.", step=5, selector="[data-tour='ads-approve']"),
    _h("ads_builder.proposal.share", "The link, and the three answers to it",
       "A client can say yes, yes with my changes, or let\u2019s talk — the middle "
       "one is the most common real answer and an approve/reject pair forces it "
       "into whichever end is nearest. No answer yet stays gray rather than "
       "reading as a no. A change request carries the name and email of whoever "
       "asked, because three people at one company will disagree with each "
       "other. Revoked, deleted and never-existed all answer the same 404, so "
       "somebody probing tokens learns nothing.",
       step=6, selector="[data-tour='ads-share']"),
    _h("ads_builder.proposal.cpc", "Three numbers, and only one of them is a cost",
       "Without a measurement this is our sector benchmark \u2014 an opening "
       "figure, labeled as one. Ask Google and it prices this campaign\u2019s own "
       "keywords in its own target areas, but the two things Google returns are "
       "not interchangeable: a top-of-page bid is what you would have to bid to "
       "show at the top, always the larger figure, and never what you pay; the "
       "forecast average CPC is. Whichever you are looking at is named, with its "
       "own caveat, here and on the client\u2019s estimate. An area Google could "
       "not place is named rather than quietly widened, because a CPC measured "
       "across three of five counties is not this campaign\u2019s. Measuring "
       "re-costs the tiers too, so the page cannot show a measured headline over "
       "tiers priced at the sector rate.",
       selector="[data-tour='ads-cpc']"),
    _h("ads_builder.proposal.client_record", "Filed, or filed in one of two places",
       "Generating writes the proposal onto the client record as a live link "
       "rather than a PDF snapshot — it gains comments and changes status, and "
       "a copy sitting on the record would end up contradicting it — and logs "
       "the work. Each write is reported separately here, because \u201cattached\u201d "
       "and \u201cattached in one of two places\u201d are different outcomes and one "
       "tick for both is how people learn not to trust the tick."),
    _h("ads_builder.proposal.launch", "Two routes, and one of them works today",
       "The Ads Editor CSV imports under the account owner\u2019s own sign-in and "
       "needs no API access at all, so an approved campaign can reach the client "
       "account this afternoon; the build sheet beside it lists the assets "
       "Editor cannot carry and anything the proposal is still missing. The API "
       "route needs a developer token Google approves on its own timetable, and "
       "deploys the identical proposal unchanged once it lands. Either way "
       "every campaign is created paused — nothing spends until a human enables "
       "it.", step=7, selector="[data-tour='ads-launch']"),

    _h("ads_builder.approvals.blocking", "The press everything else waits on",
       "An estimate that has not been approved cannot be sent to a client at "
       "all. That was visible only inside each proposal, so this queue read as "
       "\u201cnothing to do\u201d while every row in it waited on the same one press. "
       "Archived proposals are left out of this band: nobody is going to "
       "approve those.", step=1, selector="[data-tour='ads-blocking']"),
    _h("ads_builder.approvals.colors", "Four states, not two",
       "Green is approved as presented, yellow is approved with changes "
       "attached, red is wants a conversation. Gray is no answer yet — which is "
       "not a fourth kind of bad: not sent, sent and ignored, and they said no "
       "are three different situations and only one of them is finished.",
       step=2, selector="[data-tour='ads-colors']"),

    _h("ads_builder.campaigns.token", "The one screen that needs Google",
       "Live campaigns reads the Google Ads API, which needs a developer token "
       "Google approves on its own timetable. Everything before it — "
       "generating, reviewing, approving, sending the estimate and the Ads "
       "Editor export — is the Hub\u2019s own and works without it, which is why "
       "the tool no longer opens on this screen.",
       step=1, selector="[data-tour='ads-live']"),
    _h("ads_builder.campaigns.paused", "Everything arrives paused",
       "A deploy is one atomic mutate: if any single operation fails Google "
       "rolls the whole batch back, so a half-built campaign cannot happen. "
       "What lands is paused, and stays paused until a person enables it here "
       "or in Google Ads.", step=2, selector="[data-tour='ads-paused']"),

    _h("ads_builder.optimization.accounts", "Start with the account, not a campaign",
       "This rail contains only active client accounts. Each score and Google "
       "recommendation count loads independently for the ten accounts on the "
       "current page, so one slow account cannot hold up the rest of the book. "
       "Opening an account runs the detailed "
       "search-term, keyword, schedule and diagnostics scan only for that "
       "client, which keeps the first page useful even with a large manager "
       "account.", step=1, selector="[data-tour='ads-opt-accounts']"),
    _h("ads_builder.optimization.queue", "One ordered list of what needs doing",
       "Google's optimization score and recommendations sit beside Smart 1's "
       "measured checks for expensive clicks, search terms with spend and no "
       "conversions, redundant keywords and weak day-and-hour blocks. The "
       "filters change only what you see; they never dismiss or apply an item. "
       "Schedule findings are deliberately review-only because one short date "
       "range is not enough evidence to rewrite when a campaign serves.",
       step=2, selector="[data-tour='ads-opt-queue']"),
    _h("ads_builder.optimization.actions", "Every change is a separate approval",
       "The menu opens one exact Google Ads operation with its account, campaign "
       "and proposed values visible and editable. Additions and search-term "
       "decisions run through AI before the approval button is enabled. There is "
       "no second confirmation and no bulk apply. New positive keywords are "
       "created paused for a final review; a negative is exact by default so a "
       "single poor query does not block a broader class of useful traffic. AI "
       "can draft keywords, sitelinks and an image direction, but a draft has "
       "no path around this approval step.",
       step=3, selector="[data-tour='ads-opt-actions']"),
    _h("ads_builder.optimization.safety", "A scan is read-only",
       "Opening this screen and pressing Scan only reads Google Ads. Even a "
       "Google recommendation stays a proposal here until its own approval is "
       "given. This distinction matters because recommendations can change as "
       "the account changes; the screen refreshes after an approved operation "
       "instead of assuming the old queue is still current.",
       step=4, selector="[data-tour='ads-opt-safety']"),

    _h("ads_builder.activity.mirror", "This log, and the Hub\u2019s",
       "Every generation, status change, deployment and API error is written "
       "here and mirrored into the Hub\u2019s own activity log, which is what "
       "puts it on the client\u2019s 360 record. The mirror used to raise on "
       "every call and be swallowed by the except beside it, so this page "
       "looked complete while nothing Smart 1 Ads did ever reached the client "
       "record. If a campaign is missing from a client\u2019s history and is "
       "listed here, that is the half that has broken.",
       step=1, selector="[data-tour='ads-log']"),
    _h("ads_builder.activity.errors", "An API error is logged, not raised at you",
       "A refused deploy or a Google error lands here in red with the response "
       "beside it, rather than only in the toast that has since scrolled away. "
       "It is the first place to look when a deploy \u201cdid nothing\u201d.",
       step=2, selector="[data-tour='ads-log-filter']"),

    _h("ads_builder.settings.status", "What is unavailable, not what is down",
       "Each missing variable is named with what it actually costs, because "
       "three of the four steps in this tool need none of them. A tool "
       "described as down when only its last step is unavailable is how a "
       "working generator went unused for months.",
       step=1, selector="[data-tour='ads-status']"),
    _h("ads_builder.settings.tier", "A token that works is not a token that measures",
       "A developer token carries an access tier, and the tiers differ in what "
       "they may call. Google grants a new one Explorer access automatically: "
       "production accounts, 2,880 operations a day, and the keyword planning "
       "services excluded \u2014 so a perfectly healthy token answers "
       "DEVELOPER_TOKEN_NOT_APPROVED to a cost-per-click request. Read as a bad "
       "key that sends somebody to rotate a credential that was fine. Basic "
       "access is the first tier that can measure anything, and it is applied "
       "for and reviewed rather than granted. Google publishes the tier nowhere "
       "an API can read it, so a value typed here is a claim and this check is "
       "the only observation \u2014 which is why it is a button: it spends an "
       "operation against the cap a deploy also needs."),
    _h("ads_builder.settings.openai", "The key is the deployment\u2019s, not yours",
       "The generator reads OPENAI_API_KEY from this service at call time — the "
       "same key the SEO, FAQ and proposal tools use. This page will never ask "
       "you to paste one: a form asking for a key reads as \u201cthis page needs a "
       "key from me\u201d on a Hub that has had one set all along.",
       step=2, selector="[data-tour='ads-openai']"),


    # ---------------- Commercial Builder ----------------
    #
    # This module had no explanation on any screen — no bubbles, no tour —
    # while every screen of it makes a decision that costs money or gets a
    # spot refused. The same failure Smart 1 Ads had: hub/help.py,
    # hub/help_routes.py and hub-help.js were all working, and nothing reports
    # a screen that placed neither a dot nor a data-screen.
    #
    # Per screen, not per module. The one thing that made Smart 1 Ads' help
    # worse than absent was a module-wide walkthrough offered on screens whose
    # selectors did not exist there, so "Do it for me" returned in silence.

    # ---------------- The two HyperFrames tools ----------------
    # Placed on the standalone screens only. Deliberately no `step=` and no
    # `data-screen` on either template: a tour is offered where one is
    # registered, and naming a screen with no steps is the silence Smart 1 Ads
    # shipped on Settings and Live campaigns.

    _h("paint_animation.what", "A treatment, not a spot",
       "This draws one thing painting itself on \u2014 a line of copy, a logo, a "
       "product shot. It is a piece of a commercial rather than a whole one: "
       "good for a logo reveal, a hand-drawn underline under an offer, or a "
       "short social clip. For the whole piece use the Vox Explainer, or the "
       "Commercial Builder if it is a spot somebody is buying airtime for. It "
       "renders on our own service rather than a paid provider, so a render "
       "costs nothing per press \u2014 what it costs is a few minutes."),

    _h("paint_animation.style", "Three modes, and they want different inputs",
       "Handwriting writes copy on stroke by stroke and wants words. Paint-on "
       "paints a picture in and wants an image \u2014 a logo or a product shot. "
       "Living painting gives a still continuous motion, which is what to pick "
       "for something that has to hold under narration. Give it the wrong "
       "input and it still renders; it just has nothing to do."),

    _h("paint_animation.client", "Optional, and it is what keeps the file",
       "Leave it blank and the clip is yours to download and nothing is filed "
       "anywhere. Pick a client and the finished file is copied into their "
       "Cloudinary library and written onto their 360 record \u2014 which is the "
       "only way it survives, because the link the render service gives back "
       "is swept. It has to be a client we actually have: a name that matches "
       "nothing is refused rather than filed under a client nothing joins to."),

    _h("vox_explainer.what", "A 60\u201390 second argument, not a commercial",
       "An editorial collage piece \u2014 typography and cut-out imagery making "
       "one case in a numbered sequence of claims. It is a YouTube or social "
       "piece; nobody sells a slot this length on CTV, which is why the "
       "Commercial Builder only offers it on those two platforms. Everything "
       "after the beat list is deterministic: the same beats always render the "
       "same film."),

    _h("vox_explainer.source", "It only knows what you give it",
       "A topic, a pasted document, or a link \u2014 and a link is fetched and "
       "read rather than guessed at. Nothing outside what you supply reaches "
       "the beats: the model is told not to invent a statistic, a price, a date "
       "or a quotation, and a beat it cannot support is meant to be left out. "
       "With no OpenAI key set you still get an outline, built from your own "
       "sentences, and the screen says that is what happened."),

    _h("vox_explainer.beats", "Read these before you render",
       "Each beat is one claim: a headline, a line of support, and how it is "
       "drawn. Rendering takes a few minutes, so this list is the only thing "
       "you can correct before it \u2014 which is why writing the beats and "
       "rendering them are two separate presses. Anything you edit is held to "
       "the same rules the written version is, and the seconds are rebalanced "
       "to fit the window rather than trusted."),

    _h("vox_explainer.client", "Optional, and it is what keeps the file",
       "Same as the paint tool: blank means the render is yours to download "
       "and nothing is filed. Pick a client and it is copied into their "
       "library and written onto their record, which is what stops it "
       "disappearing when the render service sweeps its own output."),

    _h("commercial_builder.start.client", "Pick the client, don\u2019t retype them",
       "The first option searches the agency\u2019s real client list. Take it where "
       "you can: a commercial filed under a name typed by hand joins to nothing "
       "\u2014 no products, no scans, no Client 360 card, no logo or phone number "
       "on file. \u201cNew business\u201d is for somebody we are pitching, who "
       "genuinely has no client record yet. If the search comes back empty, read "
       "what it says: no such client and \u201cwe could not read the client list\u201d "
       "are different answers and only the first means create them as new.",
       step=1, selector="#client-mode"),
    _h("commercial_builder.start.platform", "Platform changes the spot, not the crop",
       "A social cut is not a vertical CTV spot. It opens on its hook inside two "
       "seconds because a feed has no slot holding the viewer in place, and it "
       "carries its claims as on-screen text because most of that audience is "
       "watching on mute \u2014 both are checked, not merely suggested to the "
       "writer. CTV leans on a QR code instead, because a remote cannot click. "
       "Picking this wrong gives you a spot that plays and does not work.",
       step=2, selector="#platform-choices"),
    _h("commercial_builder.start.lengths", "Several lengths, one concept",
       "Tick as many as you need and each becomes its own commercial \u2014 its own "
       "script, scenes and render \u2014 built from one shared concept so they say "
       "the same thing. Building the :15 afterwards instead means walking the "
       "wizard again and getting a different idea out of it. A :60 costs about "
       "twice a :30 in AI video credits, voiceover characters and render time, "
       "and on skippable inventory it is the length viewers skip most; the note "
       "under the picker says so when you tick it. Watch for the red note too: "
       "the published spec sells Connected TV at 15\u201330 seconds, so a :05, a "
       ":06 or a :60 CTV cut is outside what that buy takes. The :06 is a "
       "YouTube bumper \u2014 one idea, unskippable, and scored on none of the "
       "pacing rules the longer lengths are.",
       step=3, selector="#length-choices"),
    _h("commercial_builder.start.publishers", "Which streaming platforms, and why it is asked",
       "Optional, and it changes nothing about how the spot is built \u2014 it "
       "exists so the tool can warn you when a publisher refuses something you "
       "have switched on. The one that matters today is Amazon: Amazon Streaming "
       "TV supports no QR code at all, and its own creative guidance says an ad "
       "should not carry call-to-action elements that encourage clicking, "
       "because there is nothing there to click. Tick Amazon and the CTA step "
       "says so while the end card is still being built, rather than at "
       "trafficking. Leave it blank and nothing is assumed \u2014 no warning is "
       "not the same as a publisher that allows it.",
       step=4, selector="#publisher-choices"),

    _h("commercial_builder.brief.what", "This is what the whole spot is built from",
       "Everything downstream reads this: the three concepts, the timed script, "
       "the stock searches and the end card\u2019s offer. Write the offer as a client "
       "would say it \u2014 \u201c$79 air conditioning tune-up before August 31\u201d "
       "rather than \u201cHVAC services\u201d."),
    _h("commercial_builder.brief.archetype", "What the spot is, not how it gets made",
       "The commercial type on the Start page answers a different question \u2014 "
       "stock footage or an AI spokesperson is how it gets BUILT. This is what "
       "it IS: the narrative the viewer actually watches. They were one field "
       "for a while, which meant picking \u201cAI spokesperson\u201d said "
       "nothing about the story and picking \u201cTestimonial\u201d said nothing "
       "about the method, and the writer was told half of what had been decided. "
       "Each one names what it is good at, what it is bad at, and \u2014 the part "
       "worth reading \u2014 what it needs from the client. A testimonial needs a "
       "real customer who has agreed. A before-and-after needs the BEFORE, which "
       "nobody photographs because at the time it was just a Tuesday. That "
       "question appears the moment you pick one, and an unanswered one shows on "
       "the Blueprint as something to sort out while it is still free to change. "
       "Where the client's industry is on file, the ones that usually work in "
       "that category are named \u2014 as a suggestion, never a filter, because an "
       "unusual choice is often the reason a spot works. Nothing picked yet "
       "means the tool has inferred one from the commercial type, and it says "
       "so rather than drawing a selection you never made."),
    _h("commercial_builder.brief.landing", "The landing page becomes the QR code",
       "Where there is one, this is what the QR code on the end card points at, "
       "with tracking added so scans report as their own source rather than as "
       "direct traffic. Without it the code falls back to the website on the CTA "
       "card and then to the client\u2019s home page \u2014 which throws away the offer "
       "the viewer just watched. Nothing is ever guessed at: with none of the "
       "three, the code is refused rather than pointed somewhere invented."),

    _h("commercial_builder.blueprint.beats", "The beats are the length\u2019s, not an even split",
       "A :30 is a hook, the value, and a close that holds the end card long "
       "enough to act on \u2014 not three equal thirds. The percentages are where "
       "each beat starts and ends, and the script was written onto them. On a "
       "social buy the first beat is different again: the hook is at zero, "
       "because the thumb is already moving.",
       step=1, selector="[data-tour='cb-beats']"),
    _h("commercial_builder.blueprint.abcd", "Somebody else\u2019s numbers, not ours",
       "Google open-sources the evaluator it machine-scores YouTube creative "
       "with, and every threshold on this card carries the name of whoever set "
       "it \u2014 which is the whole point: \u201cyour average shot is ten seconds "
       "and Google\u2019s own detector wants two\u201d is an argument a client "
       "cannot talk you out of, where \u201cour tool thinks this is slow\u201d is "
       "an opinion. It scores the plan rather than a finished file, so a pacing "
       "problem is found while it is still free to fix. A row that says "
       "\u201cnot measured\u201d needs the rendered frame and is never a tick. A "
       ":06 bumper is scored on none of the pacing rules \u2014 cutting one to a "
       "two-second average would be three cuts a second, which is a strobe, not "
       "a bumper.",
       step=2, selector="#abcd-rows"),
    _h("commercial_builder.blueprint.compliance", "Which rules this copy puts in play",
       "This tool renders finished, deliverable video, and some copy engages "
       "published advertising rules. A payment, a rate or \u201cno money "
       "down\u201d engages Truth in Lending. A testimonial engages the FTC\u2019s "
       "endorsement guides. A law firm, a broker-dealer or a brewery brings its "
       "own regime with it. Each row names the rule, the authority behind it and "
       "what it requires \u2014 so the conversation happens while the script is "
       "still being written rather than after the spot has run. "
       "\u201cEngaged by\u201d quotes the words that put it in play, so you can "
       "find them in the script. "
       "It never says the spot is compliant, and it cannot: that is a judgment "
       "about a specific ad in a specific state and it belongs to the client\u2019s "
       "counsel or compliance officer. Nothing here blocks a render either. What "
       "it asks for is one acknowledgment before a rendered cut is FILED \u2014 a "
       "record that these were put in front of a named person, which is why a "
       "shared login cannot give one. Rewrite the offer afterwards and that "
       "sign-off is retired, because it was a statement about the copy as it was.",
       step=3, selector="#compliance-card"),
    _h("commercial_builder.blueprint.checks", "The same checks Render runs",
       "They were only on the last step, and every one of them is about "
       "something on this screen: a scene with no footage, a clip shorter than "
       "the scene it sits in, narration outside the word budget. A red cross "
       "blocks the render; an amber mark is a recommendation and will not. "
       "\u201cPublished spec\u201d is the creative spec kit \u2014 the one the people "
       "buying this inventory work from \u2014 and it is checked on the plan, before "
       "a frame exists, because length and aspect ratio are what a platform "
       "refuses creative over and both are decided here.",
       step=4, selector="#run-checks-btn"),
    _h("commercial_builder.blueprint.narration", "A longer spot needs more script, not longer pauses",
       "The script writer sizes the read once, against the word budget for this "
       "length, and stops \u2014 which is why a :60 can come back reading like a :30 "
       "with gaps in it. This writes more, inside the room the budget actually "
       "has, and re-measures. When there is no room left it says so rather than "
       "quietly doing nothing: shorten a line first, or build a longer cut. A "
       "scene you have locked is never rewritten under you.",
       step=5, selector="#expand-narration-btn"),
    _h("commercial_builder.blueprint.assets", "Make a frame, then animate it",
       "The two AI buttons are one job in order, not two ways of doing the same "
       "thing. Runway animates a starting image and has no usable text-only "
       "path, so step 2 cannot run until step 1 (or stock, or an upload) has "
       "given the scene a frame \u2014 which is why it stays disabled until then. "
       "Clips come back at 5 or 10 seconds and nothing else, so a scene longer "
       "than 10 seconds is refused rather than handed a clip that stops early. "
       "Footage we already own is listed first and badged OWNED: it costs "
       "nothing and needs no license check.",
       step=6, selector=".cb-step-pair"),
    _h("commercial_builder.blueprint.sfx", "A sound effect is sourced, like footage",
       "Two drafts per press, and nothing is generated until you press \u2014 it is "
       "billed per generation, like the stills and the AI video beside it. "
       "Leave the length blank and the model reads it from the description, "
       "which is usually right: a door slam and a bed of rain are not the "
       "same length. Anything longer than this shot is trimmed to it at the "
       "render, so an effect meant to carry across two shots wants to be on "
       "the second one as well. It ducks under the narration automatically, "
       "by the same amount the music does.",
       step=7, selector=".sfx-btn"),

    _h("commercial_builder.vox.source", "It only knows what you give it",
       "A topic, a pasted document, or a link \u2014 and a link is fetched and "
       "read rather than guessed at. Nothing outside what you supply reaches "
       "the beats: no invented statistic, price, date or quotation, and a beat "
       "the material cannot support is left out rather than filled in. With no "
       "OpenAI key you still get an outline, built from your own sentences, "
       "and the screen says so."),

    _h("commercial_builder.vox.beats", "A beat list, not a storyboard",
       "This spot has no scenes: a Vox explainer is rendered whole from these "
       "beats, which is why it has this step instead of Concepts, Blueprint, "
       "Voice and CTA. Each beat is one claim. Read them before you render \u2014 "
       "rendering is a few minutes and this list is the only thing you can "
       "correct before it. Anything you edit is held to the same rules the "
       "written version is, and the seconds are rebalanced into the "
       "60\u201390 second window rather than trusted."),

    _h("commercial_builder.voice.cast", "Say what it should sound like, then listen",
       "Ranked against the account\u2019s own voices by the same casting rules the "
       "Radio Promo builder uses. Play the samples before you cast \u2014 the "
       "ranking reads the labels ElevenLabs publishes, and a cloned voice "
       "carries none, so a voice can come top having matched nothing. The note "
       "beside the button says which of those happened.",
       step=1, selector="#voice-wants"),
    _h("commercial_builder.voice.settings", "Stability and style, and what they trade",
       "Lower stability gives a more expressive, less predictable read; higher "
       "makes it steadier and flatter. Style pushes it towards the character in "
       "the voice\u2019s own samples. Both are worth a preview rather than a guess \u2014 "
       "a :30 read is cheap to regenerate and expensive to notice at the render.",
       step=2, selector="#voice-preview-btn"),
    _h("commercial_builder.voice.pronunciation", "Local names, said properly",
       "Kept against the client, not this spot, so \u201cGahanna\u201d is right in "
       "every commercial they ever get. Write it as it sounds \u2014 guh-HAN-uh.",
       step=3, selector="#pron-rows"),
    _h("commercial_builder.voice.music", "The bed ducks under the read automatically",
       "Level is how loud the music sits when nobody is speaking; it drops "
       "under the voiceover on its own. High is for a spot carried by energy "
       "rather than by what is said \u2014 on a feed, where most people watch "
       "muted, it is doing very little.",
       step=4, selector="#music-mood-choices"),
    _h("commercial_builder.voice.compose", "The bed is composed, not picked off a shelf",
       "This step used to capture a mood and a level and generate nothing, so "
       "the level was ducking a track that did not exist. The prompt here is "
       "what ElevenLabs is actually asked for \u2014 press a mood above to fill it "
       "in, or write your own. It is composed to this spot\u2019s own runway, so "
       "the track never has to be trimmed to fit, and it is billed per "
       "generation: nothing happens until you press. Two options come back "
       "because one is a coin toss; the one you keep goes on the timeline and "
       "the level above is what ducks it under the read.",
       step=5, selector="#music-prompt"),

    _h("commercial_builder.cta.style", "The end card is the only part that asks for anything",
       "Style decides what dominates the frame: the logo, the offer, the "
       "website or the phone number. Pick the one the client actually wants "
       "acted on \u2014 a card that shows all four equally gets none of them "
       "remembered.",
       step=1, selector="#cta-style-choices"),
    _h("commercial_builder.cta.qr", "On CTV this is the whole response mechanism",
       "There is nothing to click on a television, so a Connected TV spot "
       "without a code asks for a response it has given nobody a way to make. "
       "It is switched on by default on a CTV buy for that reason \u2014 but it "
       "is a recommendation, not a requirement: several publishers take no code "
       "at all, and a check that refuses to render a spot for one of them would "
       "be this tool insisting on something the platform forbids. Amazon "
       "Streaming TV is the case that matters today; tick it on the Start page "
       "and this step warns you. Where a code is on, it has to be "
       "high-contrast, big enough, and held long enough to pull out a phone "
       "\u2014 all three are checked. On social it is off: the ad is already "
       "tappable, and a code asks somebody to scan the phone they are holding. "
       "A :05 or :06 is too short to scan at all. The panel underneath says "
       "exactly where scanning it goes and which Smart 1 Suite account will "
       "count the scan; a client with their own sub-account gets their own, and "
       "a business we are pitching is filed to Smart 1 Marketing, which is "
       "where prospects live.",
       step=2, selector="#cta-qr-enabled"),
    _h("commercial_builder.cta.logo", "For the viewer who looked away",
       "A small corner bug keeps the brand on screen for the whole spot rather "
       "than only on the end card, which matters most on CTV where the spot is "
       "often heard rather than watched. It is a recommendation, not a "
       "requirement \u2014 the check will mark it amber, not block the render.",
       step=3, selector="#cta-logo-persistent"),

    _h("commercial_builder.preview.review", "The client answers on a link, not in an email",
       "Rendering makes a file; this makes a page the client opens with no "
       "login, watches the cut on, leaves notes against a timestamp, and "
       "answers. There is no mail sender in the Hub, so the link is made here "
       "and you send it \u2014 anyone holding it can answer, which is the point: "
       "the marketing manager forwards it to the owner and both replies are "
       "kept. There are three answers rather than two, because \u201cyes, but "
       "fix the phone number\u201d forced into approve-or-reject goes to "
       "whichever end is nearest. If two people disagree, the most restrictive "
       "answer is the one that stands \u2014 a colleague\u2019s \u201clooks "
       "good\u201d must not overwrite somebody else\u2019s \u201cyou cannot say "
       "that\u201d \u2014 and both replies are listed. A spot the client asked "
       "changes on will not file until you make them or say you have settled "
       "it another way, and filing it anyway is recorded as exactly that. "
       "Rounds are counted to four; a fifth is not refused, it is flagged, "
       "because a client turned away from the page just gets emailed the file "
       "and every note goes back to being untraceable."),
    _h("commercial_builder.preview.render", "One size, then the next",
       "Rendering all three sizes at once means the second and third come off "
       "a storyboard nobody has watched yet \u2014 so a note on the first applies "
       "to two cuts that have already been paid for. Render one, watch it, "
       "then approve it. Approving is what files it: the video is copied into "
       "the client\u2019s library and recorded on their record, and the panel "
       "says which of those two actually happened rather than showing one tick "
       "for both. Nothing is filed before you approve it. Where several "
       "lengths were started together they are built :30 first \u2014 the others "
       "are cut down from its storyboard \u2014 then :15, the :06, the :05, "
       "and the :60 last, because the :60 is the most expensive and the "
       "first to be dropped when the budget lands. Approving one hands you "
       "the next one\u2019s Blueprint. Once one cut of a spot has been "
       "approved the rest come off a storyboard somebody has watched, so "
       "they can be sent together \u2014 that is what \u201cRender the "
       "other\u201d does, and it is not offered before the first approval."),

    _h("commercial_builder.dashboard.reviews", "What has come back from clients",
       "A client answering a review link used to reach the activity log and "
       "nothing else, so the only way to find out was to open the spot and "
       "look. There is no mail sender in the Hub, so the answers are put "
       "here instead. A round counts as answered if somebody pressed one of "
       "the three buttons \u2014 or left notes and pressed none, which is "
       "still a reply. A spot with a filed cut leaves the list, because the "
       "answer has been acted on. Rounds still out with the client are "
       "counted separately, in the corner, since nobody here is holding "
       "those up. And an empty list says which kind of empty it is: nothing "
       "waiting, nothing yet sent, and a table that could not be read are "
       "three different situations and only two of them mean there is "
       "nothing to do."),

    # ---------------- Google Finder ----------------
    # Six tiles on /tools and not one of them had any explanation behind it,
    # which hub/help_coverage.py reports as half the tool book. The screens
    # here are unusual in one way worth saying out loud on each of them: what
    # they show is the *stored index* the scheduler sweeps every three hours,
    # not a live read of Google -- so an empty platform is as likely to mean
    # "that token was refused" as "there is nothing there", and the sweep's
    # own per-platform note is the only thing that tells them apart.
    _h("google_finder.overview", "What this is a list of",
       "Every GA4 property, Tag Manager container, Search Console site and "
       "Business Profile that any connected Google login can see, swept into "
       "one index and joined to a client where we can work out whose it is. "
       "It is the stored sweep rather than a live read, so it is up to three "
       "hours old and Refresh forces it. Read the per-platform note before "
       "you read a count: a login whose token was refused shows the same "
       "empty list as one that genuinely owns nothing, and only the note "
       "separates them. Resources this could not put a name to are not lost "
       "— they are the orphan list on Match Google Accounts, which is "
       "where you say whose they are.",
       link="/tools/google-match", link_text="Match Google Accounts"),
    _h("google_finder.ga4", "Comparing two periods, and what the AI adds",
       "Runs one GA4 property over a chosen period against a comparison "
       "period and writes the read-out. The filters narrow what is being "
       "compared before the model sees it — a source/medium or a page "
       "path filter changes the numbers themselves, not just the wording, so "
       "set those first. The persona and sentiment presets change only how "
       "the finding is phrased, never the figures underneath it. What comes "
       "back is a draft for somebody who knows the account: it explains the "
       "movement in the data it was given and cannot know about the sale, "
       "the outage or the campaign that caused it."),
    _h("google_finder.gtm", "Deploying a pixel into somebody's container",
       "Finds the Tag Manager containers a login can reach and writes a tag "
       "into one of them. It lands in the container's workspace, not "
       "live — publishing is still a deliberate press inside Tag "
       "Manager, which is what keeps a wrong paste off a client's site. "
       "Check the account and container are the ones you mean before you "
       "deploy: containers are routinely named after the agency that built "
       "them rather than the business, which is the same trap that used to "
       "file thirty-seven franchises under their media partner. Tag Manager "
       "rate-limits harder than the other platforms, so a sweep here paces "
       "itself and an account that was throttled carries its previous "
       "reading rather than reporting nothing."),
    _h("google_finder.search_console", "Adding domains and sitemaps in bulk",
       "Takes a list of domains and their sitemaps and registers them "
       "against one Google account in Search Console, one per line, instead "
       "of a dozen trips through their UI. It is the same verification "
       "Google would ask for either way — a domain the account cannot "
       "already prove it owns will be refused by Google, and that refusal is "
       "reported per line rather than failing the batch. Nothing here "
       "removes a property."),
    _h("google_finder.business_profile", "Why this one is often switched off",
       "Generates the links that send a client straight to the review or "
       "listing action you want them to take, rather than to the profile and "
       "a hunt. The Business Profile APIs need per-project access granted by "
       "Google on top of the ordinary OAuth scope, so this platform sits "
       "behind its own switch and reads as *not asked* rather than *empty* "
       "when it is off — those are different answers, and only one of "
       "them is something to chase."),
    _h("google_finder.history", "What was asked, and what was written",
       "Two different records, searchable side by side: the audit log of "
       "what this tool actually did — every sweep, deployment and "
       "attachment, with who and when — and the reports somebody saved "
       "out of GA4 Tools. Come here when a property changed hands, a tag "
       "appeared that nobody remembers deploying, or you want the read-out "
       "from last quarter without running the comparison again and getting "
       "slightly different numbers."),

    # ---------------- Media calculators ----------------
    # Five tiles on /tools with nothing behind them. The fact worth putting on
    # the screen is the one thing true of all of them and stated on none: the
    # staff copy and the copy a prospect opens are the same fields and the same
    # catalog.run(), differing only in whether the plan is withheld until a
    # contact is captured. They look almost identical and are one URL apart,
    # which is exactly how a rep ends up filing a live client as a fresh lead.
    _h("calculators.index", "What these are, and where else they run",
       "Media calculators that size a buy from a budget and a market \u2014 "
       "reach, frequency and delivery, at the rates we actually sell at. Each "
       "one runs in three places off this same code: the staff page here, a "
       "public link an ad can point at, and framed inside a page on "
       "smart1marketing.com. There is one implementation of the arithmetic, "
       "so the three cannot quote a client different numbers; what differs is "
       "only whether the plan is shown straight away or held back until "
       "somebody has left their details."),
    _h("calculators.internal", "This is the staff copy, and it captures nothing",
       "Same fields and the same arithmetic as the version a prospect opens, "
       "with the whole plan returned in one go rather than held back behind a "
       "name, an email and a phone number. Nothing here is stored: no "
       "estimate, no contact, and nothing sent to Smart 1 Suite. That is the "
       "point of it \u2014 sizing a buy for a client of eleven years' standing "
       "through the public form meant typing some contact into it, and "
       "whatever got typed landed in the leads panel reading exactly like a "
       "live prospect. The client-facing copy is one URL away and looks "
       "almost identical, so check which one you are on before you send a "
       "link: this page is behind the staff login and a prospect cannot open "
       "it."),

    # ------------- The twelve tools the coverage report named -------------
    # hub/help_coverage.py measures the tiles on Creative and Client Tools
    # against this registry, and twelve tiled tools had no help written at
    # all -- the backlog that report exists to hold, written down to zero
    # here. Every key is placed by the tool's own staff template, guarded
    # `if help_dot is defined` like every call in this Hub; the PDF
    # Optimizer's page is a static file with no Jinja, so it carries the raw
    # span help_dot() emits instead. None of it reaches a client-facing
    # page -- Fan Radio's /r/ link, the picker's /pick/ page, Google
    # Access's /connect flow and the built landing pages are outside the
    # help layer on purpose, because a staff note on a page a client reads
    # is an internal note in front of somebody we are selling to.

    # ---------------- Image Optimizer & Resizer ----------------
    _h("image_optimizer.tool.intro", "What this tool is for",
       "One image to an exact size, format and weight, handed straight "
       "back. For a batch renamed and filed for SEO use the SEO Image "
       "Pipeline; for images already live on a client's page use the Page "
       "Image Optimizer. This is the one-off — a 300x250 for an ad "
       "slot, a hero cut down before it goes anywhere, a logo at the size "
       "a directory demands. A file that cannot be read is refused in "
       "plain words, and that answer is about the file rather than the "
       "tool."),
    _h("image_optimizer.tool.crop", "Crop first, then size",
       "Cropping runs before the resize, so the width and height below "
       "describe the cropped area rather than the original. Pick the "
       "shape here and the exact pixels underneath; the ratio presets are "
       "the slots these images usually end up in. On an animated GIF the "
       "crop is applied to every frame, so the animation survives it."),
    _h("image_optimizer.tool.resize", "Cap the pixels before anything else",
       "Dimensions matter more than format or quality: a 6000-pixel "
       "camera photo converted without a resize is still 6000 pixels "
       "wide, and still enormous. Set the size the image will actually be "
       "displayed at and most of the saving follows from that alone. Lock "
       "aspect ratio keeps the shape while you type either number; the "
       "presets are the sizes this Hub is asked for most."),
    _h("image_optimizer.tool.output", "Format, and what a target size can promise",
       "JPG for photographs, PNG for graphics and anything that needs "
       "transparency, GIF only for animation — an animated GIF saved "
       "as a GIF keeps its animation, and saved as PNG or JPG it becomes "
       "its first frame. A target size is honored by stepping quality and "
       "then dimensions down, and the stepping stops at a 160-pixel short "
       "side: a very small target can come back larger than asked, and "
       "what downloads is the closest the format could honestly get."),

    # ---------------- Page Image Optimizer ----------------
    _h("page_images.scan.page", "The page is read, never touched",
       "Paste the address and the page is fetched live: every image on it "
       "is listed with its current weight and what optimizing would save. "
       "Nothing on the client's site is changed — the optimized "
       "copies are saved into their SEO images, and putting them on the "
       "page is a separate job this tool hands you the finished tags "
       "for."),
    _h("page_images.scan.client", "Who the saved copies are filed under",
       "The company name decides whose record the saved images land on, "
       "and it goes to the AI with each picture so the new filenames and "
       "alt text are written for this business rather than generically. "
       "Spell it the way the client record does — a name typed "
       "differently files the work where nobody looks for it."),
    _h("page_images.review.names", "Nothing saves until you have read these",
       "The filename and the alt text are the two things a search engine "
       "reads about an image, and both were written by a model — "
       "which is why they are shown here first. Fix anything that is not "
       "right, or skip an image to leave it out; only what is on this "
       "list when you press save is written anywhere."),
    _h("page_images.review.saved", "What saved actually means",
       "The optimized copies are in the client's SEO images — the "
       "same archive Client 360's image card reads — with the tags "
       "ready to copy. The live page still carries the old files: "
       "swapping them in happens in the client's own CMS, which is why "
       "the finished tags are handed to you here rather than left to be "
       "reconstructed later."),

    # ---------------- Client Image Uploads ----------------
    _h("image_picker.admin.intro", "A page the client uploads through",
       "Every client row has a share link that works without a login "
       "— send it and their photographs arrive in a gallery filed "
       "against their record and pushed on to Smart 1 Suite, instead of "
       "arriving by email. The same link is offered from Client 360, the "
       "IO Builder's creative checklist and both requirement PDFs, all "
       "reading one address — so wherever the ask happens, the link "
       "is the same one."),
    _h("image_picker.admin.add", "Matched exactly, or created on purpose",
       "An existing client is looked up so the gallery files against "
       "their record — matched exactly and never on a substring, "
       "because a link that collects one client's photographs into "
       "another client's gallery is worse than no link, and a name that "
       "could mean two clients proposes neither. A prospect gets a "
       "gallery with no client record behind it, which is the point: "
       "nothing is invented to file them under."),
    _h("image_picker.gallery.saved", "Reported apart, on purpose",
       "Each file says where it lives and whether it reached the "
       "client's Smart 1 Suite media library — separately, because "
       "“saved here” and “in the Suite” are different "
       "outcomes and one tick covering both is how somebody learns not "
       "to trust the tick."),
    _h("image_picker.gallery.library", "Search what is in the picture",
       "The search reads the alt text, the filename, the folder and what "
       "a vision model saw in the picture — the last one is what "
       "makes it work on the forty files nobody captioned. It runs on "
       "the server over the whole library, so the answer is about "
       "everything saved rather than about the rows this page happened "
       "to load. Deleting removes the stored copy, and for a photograph "
       "a client sent us that is often the only copy — the "
       "confirmation says so."),

    # ---------------- Landing Page Ads ----------------
    _h("landing_ads.build.intro", "One brief, every format",
       "One read of the page produces one shared brief, and the Google "
       "headline, the Meta and TikTok hooks, the banner lines and the "
       ":30 audio script are all written from it. That is the point of "
       "one tool rather than four: every format makes the same promise, "
       "and none of them promises something the page will not honor."),
    _h("landing_ads.build.setup", "Spec or attached, and whose words win",
       "Spec files under spec/ with no client attached — for "
       "pitching, or for our own campaigns; attached files under the "
       "client's name and shows on their record. The audience box "
       "overrides what the page implies, so use it when the campaign is "
       "aimed narrower than the page reads, and notes for the writer "
       "steer the emphasis without changing the facts the page "
       "supplies."),
    _h("landing_ads.build.pages", "Set the live URL once",
       "Each Smart 1 landing page is read from its live URL, and that "
       "URL is recorded here once rather than pasted per campaign — "
       "a page with no URL on file cannot be read, and says so instead "
       "of being guessed at. Every campaign built after that uses the "
       "address on this list."),

    # ---------------- Stock Photo Search ----------------
    _h("stock_photos.search.intro", "Four sources, and why ours lead",
       "Pexels, Pixabay, Unsplash and our own Cloudinary folders, "
       "searched in one pass. Photos we already own sort first and are "
       "badged, because they cost nothing, carry no third-party license "
       "and are often already the client's own brand — they used to "
       "be reachable only by scrolling the Cloudinary console, which is "
       "done by nobody. The license line appears only on the three "
       "sources that need one."),
    _h("stock_photos.search.sources", "Three kinds of empty",
       "“Nothing matched”, “that source refused” and "
       "“that folder is not in Cloudinary yet” are different "
       "answers and the page says which — only the first means "
       "change your search. A source with no key set is shown disabled "
       "and explained rather than hidden, so “why is there no "
       "Unsplash” has an answer on the screen. Copy and download go "
       "through the Hub so a use is recorded — Unsplash's terms "
       "require a ping when a photo is actually used rather than "
       "browsed."),

    # ---------------- Radio Promo ----------------
    _h("radio_promo.build.setup", "Spec now, theirs later",
       "A spec spot has no client attached and files under spec/ — "
       "it is written to win the business, and attaching it to a client "
       "later loses nothing, which is the normal path: the spec spot "
       "wins the account and the spot becomes theirs. The required "
       "disclaimer is read verbatim and counts against the word budget, "
       "so a long one buys a shorter sell."),
    _h("radio_promo.build.cast", "Cast by ear, not by list",
       "The ranking reads the labels ElevenLabs publishes against what "
       "you asked for, and it is a ranking, never a filter — a "
       "voice can come out on top having matched nothing, because a "
       "cloned voice carries no labels at all. Play the samples before "
       "you cast: a wrong voice is cheap to swap here and expensive to "
       "notice at the render."),
    _h("radio_promo.build.booth", "The voice is handed different words",
       "What the voice reads is not quite what is on screen, on "
       "purpose: numbers are said as words, and a web address or an "
       "email is spelled out the way it is said aloud — because "
       "“visit example.com” handed to a voice raw comes out "
       "wrong exactly where the whole call to action lives. The runtime "
       "is checked against the slot, with a one-button tighten when the "
       "read comes in long."),

    # ---------------- Fan Radio ----------------
    _h("fan_radio.build.setup", "Football flavor, nobody's trademark",
       "The local team is context only: it tells the writer which "
       "market and schedule it is writing around, and every word of it "
       "goes onto the block list rather than into the copy — "
       "league, club, bowl, school and fan-slogan marks are all scanned "
       "for before a spot can be delivered. A spec spot is never pushed "
       "to the Suite."),
    _h("fan_radio.build.spots", "The scan, and the post-game problem",
       "Every script is checked against the trademark block list before "
       "it can go anywhere; a hit fails the spot and the writer is "
       "re-asked once, told exactly what it broke. Post-game spots are "
       "result-neutral by default — a spot voiced on Wednesday "
       "cannot know Saturday's score, so copy that quietly assumes one "
       "is flagged, with “if it went well” / “if it "
       "didn't” alternates for the station to swap in."),
    _h("fan_radio.build.share", "One link, and the client answers on it",
       "One share link per project — random token, no login — "
       "where the client plays every spot, approves, or comments "
       "against the one it is about. Their feedback is written down "
       "before anything else happens and lands back on this screen "
       "against the spot it belongs to, so a change request cannot live "
       "only in an email thread. The page carries none of the Hub's own "
       "chrome, because what they open is theirs to read."),

    # ---------------- IO Builder ----------------
    _h("io_builder.start.sources", "Load what exists, and mind the drafts",
       "A delivered proposal, a client's record, an uploaded document "
       "or a fresh start — whatever is loaded is shown and "
       "confirmed in the interview, because a proposal quotes a price "
       "and an insertion order bills it. Unfinished IOs are listed here "
       "too, a colleague's included and marked whose: an interruption "
       "on one machine resumes on another, and a half-built order "
       "hidden away is how the same IO gets built twice. Discarding a "
       "draft is its own deliberate press, with the name in the "
       "confirmation."),
    _h("io_builder.chat.progress", "Your place is kept, on the server",
       "The interview asks one thing at a time and the order on the "
       "right builds as you answer. Everything autosaves to the server "
       "as well as to this browser, so closing the laptop mid-IO costs "
       "nothing and the draft is there from any machine. Reset is the "
       "one press that clears the saved draft too — its "
       "confirmation says so, and that sentence is true."),
    _h("io_builder.report.rates", "The rate here is what the client pays",
       "Every line comes off the shared Smart 1 rate card — the same card "
       "the Proposal Builder quotes from, so the document a client signed "
       "and the order that bills them cannot price one product two ways. "
       "What is printed here is the quoted rate rather than the card's own "
       "listed one: the listed number is the buy-side rate, and a CPM line "
       "is sold at twice it. The management and creative fee fields want an "
       "amount, a percentage, INCLUDED or NONE. NONE is a real answer and "
       "is flagged for confirmation rather than refused; what they cannot "
       "take is a sentence, because a fee field holding prose reaches "
       "whoever bills the campaign as nothing at all."),
    _h("io_builder.report.overview", "One order, one number",
       "The order number was allocated the moment the wizard started, "
       "which is why an abandoned IO leaves a gap in the numbering rather "
       "than a duplicate — a number handed out is not yet an order, and "
       "only submitting makes it one. Resubmitting a correction revises "
       "this same order rather than filing a second one, and the date it "
       "keeps is the first submission’s."),
    _h("io_builder.creative.manager", "Checked against the published spec",
       "Every file is validated against the S1M creative spec kit "
       "— the published sizes, weights and durations the people "
       "buying this inventory work from — and a refusal names the "
       "unit and the published ceiling rather than saying "
       "“invalid file”. “Creative is coming later” "
       "is a real answer and is recorded as an outstanding item, "
       "instead of reading as nothing needed."),

    _h("io_builder.creative.checklist", "What the client still has to send",
       "What each product on this order needs before it can run, and the "
       "client's own upload link at the top of it \u2014 send them that rather "
       "than asking for photographs by email, and what they upload lands in "
       "their gallery where every other tool can already see it. Files "
       "delivered through the Creative manager are checked against the S1M "
       "creative spec kit, so a size the platform would refuse is caught "
       "here rather than at delivery."),

    _h("io_builder.submit.finished", "What submitting does, and what it does not",
       "Submitting files the order in the activity log, sends it to Smart 1 "
       "Suite, and registers the business as a client if nobody here has "
       "heard of them yet \u2014 an overlay of our own, never a write to Knack, "
       "so the day the real record appears it wins. What it does **not** do "
       "is set the campaign up: that is somebody trafficking it, and an "
       "order whose products never arrive looks exactly like one that was "
       "handled. **QA \u2192 Orders With No Campaign** is the report that "
       "catches those, so an order signed in March cannot sit unbuilt "
       "until a client asks why nothing ran."),

    _h("io_builder.pdf.two", "Two documents from one record",
       "The customer PDF is what the client signs; the internal one carries "
       "what the campaign team needs and the client does not. They are "
       "generated from the same record rather than edited apart, so they "
       "cannot come to disagree about what was sold \u2014 which is why a "
       "correction goes back through the interview rather than into a PDF."),
    # ---------------- Landing Page Maker ----------------
    _h("landing_maker.pages.intro", "The page, the ads and the IO agree",
       "Builds a campaign page from a proposal: the client, the "
       "products being sold and their brand are read rather than "
       "retyped, so the page says what the proposal says. What it "
       "builds is a public page on its own link with none of the Hub's "
       "chrome on it — it is what a prospect reads, and it is "
       "often pasted onto the client's own domain."),
    _h("landing_maker.pages.new", "A client's page, or a sample for a prospect",
       "A client page is built from their proposal, which is why the "
       "client is asked first — “which proposal” is only "
       "answerable once you know whose. A prospect page is a sample "
       "built to win them, and it captures leads exactly the way a "
       "client page does, so a sample you send can bring one in before "
       "anything is signed."),
    _h("landing_maker.pages.built", "Open the link, not this list",
       "A built page is served without the staff sidebar, the help "
       "layer or the feedback tab — deliberately, since a client "
       "or a prospect is who reads it — so what you see from "
       "inside the Hub is not quite what they see. Check a page by "
       "opening its own link, the one you would actually send."),

    # ---------------- PDF Optimizer ----------------
    _h("pdf_optimizer.tool.intro", "What an error here is about",
       "Compression runs through Ghostscript and qpdf, keeping text "
       "and vector content sharp and re-compressing the images inside. "
       "When something goes wrong, the answer says which kind of wrong "
       "it is: a refusal in plain words is about your file, and a 503 "
       "pointing at the status page means this deployment is missing "
       "its tools — no document will work until that is fixed, and "
       "it is nothing to do with the PDF you are holding."),
    _h("pdf_optimizer.tool.level", "What the levels trade",
       "The levels differ in how hard the images inside the PDF are "
       "re-compressed — text and vector artwork stay sharp at "
       "every one of them. Web Optimized suits anything read on a "
       "screen; Maximum Compression squeezes hardest and visibly costs "
       "photograph quality; High Quality keeps the images closest to "
       "the original. If the result is still large, the images were "
       "already compressed about as far as they honestly go."),

    # ---------------- GPT Ads Builder ----------------
    _h("gpt_ads.pack.client", "Start from what the Hub knows",
       "Pick a client and the pack opens with their brand and their "
       "offer already read from the Hub — the point of building it "
       "here rather than in a document is that nothing is retyped and "
       "nothing is invented. Campaign is what this ad is for, in words "
       "ad operations will recognize when they read the brief."),
    _h("gpt_ads.pack.readiness", "Measured, never ticked",
       "Every requirement here is answered by measuring rather than by "
       "a checkbox: the image's pixels are read off the bytes — "
       "1:1 is required and fails, the recommended 256 square only "
       "warns — and the landing page is fetched: status, redirect "
       "chain, and whether it declares a viewport, which is what was "
       "actually measured rather than “mobile-friendly”. A "
       "check that could not run says not measured and never shows a "
       "tick. The ZIP still exports with items outstanding — they "
       "are printed at the top of the brief, so ad ops sees the gap "
       "rather than assuming they caused it."),
    _h("gpt_ads.pack.copy", "The copy checks are code",
       "A price, a percentage, a phone number or a deadline that "
       "appears in the copy and in none of the offer or brand fields a "
       "person typed is a block — a model inventing “$50 off "
       "through Friday” gets the client a phone call about an "
       "offer they never ran. An expiry date already in the past "
       "blocks too. Superlatives and over-length lines only warn, "
       "because the character limits are house guidance: the "
       "requirement sheet publishes none, and going over is not a "
       "rejection anybody has published."),

    # ---------------- Google Access ----------------
    _h("google_access.admin.intro", "One link, and what is not on it",
       "The client signs in with Google once and we are granted their "
       "Analytics, Tag Manager, Search Console and Business Profile "
       "— no screen-share, no password in an email. Google Ads is "
       "deliberately absent: Google offers no “add this "
       "email” for Ads, only a manager-link invitation needing "
       "credentials this deployment does not hold, and a tickbox that "
       "consents and then fails at our end is worse than a missing "
       "feature."),
    _h("google_access.admin.new", "Existing is matched; new becomes a lead",
       "An existing client is matched against the client list exactly "
       "— never on a substring — and a name that matches "
       "nothing is refused with New named as the way out, rather than "
       "filed where nothing joins to it. A genuinely new business is "
       "written through the lead store on the way past, so the "
       "prospect we just asked for access exists somewhere sales can "
       "see them — which is why an email or a phone number is "
       "asked for."),
    _h("google_access.admin.requests", "What a row can tell you",
       "Status is per request, and “signed in as” is the "
       "Google account that actually consented — worth a glance "
       "before chasing a failed grant, because a grant can only reach "
       "what that account itself administers, so the wrong sign-in is "
       "the first thing to rule out. A request created while a service "
       "was still offered keeps that service on its row, labeled "
       "paused, and can still be closed out."),
    _h("google_access.detail.grants", "Granted is per service, not per request",
       "Each service is granted on its own and can fail on its own "
       "— one row refusing must not read as the whole request "
       "failing. Analytics, Tag Manager and Search Console are granted "
       "by API once the client consents; Business Profile may be in "
       "manual mode, where the client gets written instructions "
       "instead, because Google gates those APIs behind its own "
       "allowlist. A service since retired still shows here and can "
       "still be marked, so a row nobody can close does not read as "
       "waiting for ever."),

    # ---------------- SmartForecast ----------------
    _h("smartforecast.dashboard.status", "What the live status means",
       "The winning trigger is the highest-priority eligible rule after "
       "lifecycle stability checks. Pausing immediately serves the approved "
       "default content while preserving rules, drafts and history, so it is "
       "the safe stop control when a campaign needs review."),
    _h("smartforecast.setup.installation", "One site, one weather location",
       "The primary postal code and timezone decide which forecast is read "
       "and when each observation belongs. The embed token is a public "
       "identifier rather than a staff credential; rotate it if the embed "
       "has been shared somewhere it should no longer load."),
    _h("smartforecast.triggers.lifecycle", "Rules move through a lifecycle",
       "Pre-event, active-event and post-event are separate approved messages "
       "for one weather rule. Ordinary forecasts must satisfy consecutive "
       "checks before activating or clearing to prevent flicker; official "
       "alerts can activate immediately."),
    _h("smartforecast.content.approval", "Draft first, publish deliberately",
       "Saving changes only the staff draft. The public embed keeps serving "
       "the last approved version until Approve & publish is pressed, so copy, "
       "CTA destinations, mobile crops and alt text can be reviewed without "
       "changing the client's website."),
    _h("smartforecast.preview.simulator", "A safe forecast rehearsal",
       "Simulation evaluates the same priority and lifecycle rules as a live "
       "weather check without changing the website. Enable the record option "
       "only when you intentionally want the scenario written as an actual "
       "observation and applied to the current state."),
    _h("smartforecast.report.history", "Evidence, not causal attribution",
       "Lifecycle history records which rule won, why and when. Engagement "
       "counts are privacy-minimized interactions with the embed; they are "
       "reported separately and do not claim that weather messaging caused a "
       "lead or sale without a controlled comparison."),

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


def has_tour(screen: str) -> bool:
    """Whether THIS screen registers tour steps of its own.

    Exact, unlike `tour()` above, which falls back to the module prefix when a
    screen has none. That fallback is right for serving a tour somebody asked
    for and wrong for deciding whether to OFFER one: asked that way, every
    screen in a module carrying any steps at all answers yes, and then draws
    another screen's steps over elements that are not on the page — a ring
    anchored to nothing, with the narration still reading confidently. Which
    is the Smart 1 Ads failure, and the reason a layout asks this rather than
    drawing data-screen on the truth of a name.
    """
    return any(h.step and h.key.startswith(screen + ".") for h in REGISTRY)


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
