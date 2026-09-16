## There is one proposal builder

There were two, which is worth remembering because the shape of the problem
recurs. `modules/sales_builder` (`/sales/builder`) and
`modules/proposal_builder` (`/sales/proposals`) shared no code, no storage and
no idea of what a campaign is. The same client could be quoted two different
ways depending on which one a rep opened, and only one produced anything an
insertion order could read.

`modules/sales_builder` is now **the** Proposal Builder. `/sales/proposals`
redirects to it (carrying Client 360's prefill through) and serves only the
old tool's archive, which stays readable because those are real documents real
clients received — `/sales/builder/api/legacy/proposals/<id>/import` reopens
one as a live quote. What moved across: the industry library (now
`hub/industries.py`), AI-written narrative copy, the Cloudinary-hosted PDF, and
filing the finished proposal onto the client record.

Delivering a proposal now files it on the client and opens an opportunity in
Smart 1 Suite, through `hub/ghl_contacts.py` — one token, one location id and
one contact write path for the whole Hub. `hub/suite_opportunity.py` keeps
only the pipeline and opportunity logic that is genuinely its own. It briefly
resolved the location itself and fell back to `GHL_COMPANY_ID`, which on this
deployment holds the same value as the company id: a companyId used as a
locationId files against the *agency*, so every opportunity would have landed
where nobody goes looking. It looks up the contact first and **asks** when
there is none,
rather than creating one from the business name — an opportunity attached to a
contact nobody can call is worse than no opportunity, and it duplicates the
real contact next time anyone searches.

### The proposal has a specification, and it is data

`hub/proposal_spec.py` owns the 13-part outline, the standing directives, the
audience partner taxonomy, the Suite tiers and the operating facts a proposal
may cite. The builder, the PDF, the Word export and the AI prompt all read it,
so changing what a proposal contains is one edit rather than four.

Three of those directives are checked rather than merely requested. Copy that
mentions **Smart 1 Labs** is discarded before a rep sees it — a prompt is a
request, and "the model was told not to" is not evidence that it did not. The
**Expected Results & ROI** section is *computed* from `hub/rate_card.py`, never
written: a management fee reports no impressions at all rather than a plausible
number, because a projection that contradicts the media plan printed above it
is worse than no projection. And the **Investment Summary** keeps recurring
platform licensing apart from media spend and one-time production, so a client
can tell what stops if they pause the campaign.

`roi`, `mediaplan` and `packages` cannot be deleted from a proposal. An older
quote saved against the previous eight-section layout keeps its copy and gains
whichever required sections it is missing on the next save.

### Discovery drives the recommendation, and the proposal is read before it is edited

The four "what are they already doing" questions were captured and never read
— a rep could answer all four and the document came out identical.
`hub/current_marketing.py` makes them mean something and adds the three that
change what we recommend: are they retargeting, are they optimised for AI
search, are they happy with their website. The gaps become the **We Suggest
They Should** list, shown in its own colour so it reads as advice rather than
another form field, and whatever the rep keeps is written into the proposal's
friction section.

The last question is the one with money behind it: are they running
traditional media, and do we *supplement* it or *move* some of that budget.
The answer sets the proposal's posture and reaches Expected Results & ROI —
but the guidance handed to the writer also **forbids arguing it**. A model
given "they want to shift budget to digital" writes a case against radio, and
a proposal that opens by calling a client's existing spend wasted loses the
room before the media plan is read. `test_proposal_spec.py` asserts those
three prohibitions are still in the text.

The Proposal Document step opens on a **preview** — the document as the client
will read it, prose and real tables — with edit, AI rewrite and hide on every
section, and the media plan editable in place so changing one budget does not
mean walking back three steps. The section-order list is still there behind a
toggle. Writing the copy runs **one request per section** so the loader can
name what it is working on and one failed section does not cost the other
twelve.

The PDF scales its type down as the document grows (`_type_scale`), bounded at
0.82. An ordinary proposal is not shrunk at all: "lower the fonts when
necessary" means when there is more than usual in it, not always.

**Any generated table can be left out, or edited.** The standing rule is that
a table is computed and the copy above it introduces one — a proposal whose
prose and figures disagree is the failure the whole specification is built
around — and that rule still decides what the tables say by default. What it
never covered is a table that is right and still wrong for *this* client: a
row naming a location under NDA, a phase of the timeline they do not want
printed, a KPI they asked us to drop. The alternative a rep actually had was
exporting to Word and editing it there, which takes the document out of the
system entirely. So `section_table()` is the single reading of "what goes
under this section" — generated, excluded, or replaced — read by the preview,
the PDF and the Word export alike, in **one** gate in front of the twelve
`kind` branches rather than in each of them, because a branch per kind is how
a setting comes to be honoured by eleven of the twelve. An edited table is
drawn in amber in the builder with the generated one one click away, and its
badge says it no longer recomputes, which is exactly true.

### The one line the rate card does not name

`hub/product_intake.CONSULTING` has defined **Consulting & Strategic
Services** all along, and its own docstring says what it is for: a line *"used
when a proposal committed to something the rate card does not name, so the
commitment reaches the insertion order rather than being dropped for lack of a
product code."* **The proposal could not make that commitment.** Every push
into `S.items` came from a rate-card row, and the card has no consulting
product — 19 categories, and the nearest thing is *Google Analytics
Consultation* under SEO, which is installing and repairing GA goals rather
than strategy work.

So the catch-all was reachable only from the IO's **intake** — the path for a
proposal uploaded as a file, read back and classified line by line. A rep
selling strategy work either left it off the quote and added it at IO time, so
the client signed a document that never mentioned it, or did not sell it. The
mechanism was built at the far end of a journey the near end could not start.

**It is still not a row on the card, and that refusal is the load-bearing
part.** `product_intake.py` says why in a comment older than this feature:
that file is the wholesale card, `check_drift()` holds it against the IO
template's embedded copy, and inventing a product inside it would make both of
those lie. There is a **third** copy in the proposal wizard, and it must not
gain the line either. So the definition is **served** rather than mirrored —
`/api/config` carries it, beside the creative sizes and the markup rule, for
the reason that section already gives — and `test_proposal_consulting.py`
requires the wizard to hand-type the product string **nowhere**.

**The join is that string, and it is exact.** The IO recognises the catch-all
by product name, so a hand-typed copy in the wizard is a line the insertion
order silently drops the day either end is edited. Asserted byte-for-byte
against the IO template's own constant.

**A description is required, and that is `question_for()`'s rule rather than a
second one.** Every consulting line ever quoted prints the same product
string, so with none the client reads *"Consulting & Strategic Services —
$5,000"* against nothing and trafficking reads a line it cannot action. It is
refused **by name** at the control rather than added blank, because a line
that reports a clean success and arrives meaningless is the whole failure
being closed. One trap in reading that rule: `question_for()` asks the basis
and the term **before** the description, so a plan line must hand over both or
the question comes back as *"monthly or one-time?"* about a line whose basis
is on the screen.

**And the description has to survive both journeys, which is where it was
actually being lost.** The IO's intake already carried it to special
instructions — but `ioDataPayload()` sent **no `specialInstructions` at all**,
so a Hub proposal converted straight through arrived with a product name and a
budget. Both ends are closed now: the client's media plan draws it under the
product (decided once in `media_plan_rows()`, drawn by the preview, the PDF
and the Word export the way `monthly_label` already is), and the conversion
writes it onto special instructions in the shape the IO's own intake appends
to, plus into `internalRequirements` under its own product heading, which is
what the internal PDF prints and what trafficking reads.

**Two of the three downstream behaviours were already right**, which is worth
knowing before anybody "fixes" them: `gated_media()` returns nothing for a
consulting plan, so it does not ask who is supplying creative for a workshop;
and `channel_lines()` leaves it out of Recommended Channel Strategy, because a
line with no rate and no gated medium is a fee rather than a channel. Both are
asserted so they stay true.

**Consulting lines dedupe on the description, not the product.** Two
engagements at two prices is the ordinary case — a strategy workshop and a
quarterly review — and every other line on the plan dedupes on a product
string all of these share.

**And there are two consulting products, which is a thing to know before
renaming either.** `state["consulting"]` is a monthly **retainer** — Suite
coaching and campaign strategy, priced from estimated hours, riding beside the
licence in the Investment Summary because it is recurring platform work a
paused campaign does not stop. This is the other one: a single **engagement**,
scoped and priced on its own, quoted on the media plan and trafficked as an
insertion-order line. Both are real, and they arrived from two directions
within a day of each other.

What that cost is the name. A client reading *"Consulting & Strategy"* in the
Investment Summary and *"Consulting & Strategic Services"* on the media plan
cannot tell which charge is which — two names for what reads as one thing,
which is the drift most of the rules in this file exist to refuse. So the
engagement is a **Strategy Engagement** everywhere a person reads it:
the plan editor, the preview, the PDF, the Word export.

**The product string underneath does not move with it.** It is the join — the
IO recognises the catch-all by that exact name, `product_intake` owns it, and
renaming it would orphan every line already quoted under it. `audit.LOG_NAMES`
and `video_library.TAG_ALIASES`' rule, one document over: the stored name
stays and the displayed one changes. `is_consulting()` keys on the product
string rather than the display name for the same reason, and
`test_proposal_consulting.py` asserts both halves — that the client's row
prints the display name, and that the join is byte-identical to the IO's
constant regardless.

### One product, one name — or the IO quietly carries 88 of 90

Two products were both called **Google Grant** — a $125 one-time setup fee and
a 15% monthly management fee — and two more were both **Local Service Ads
(LSA)**. The published rate card at `/partner/rate-card-universal` had already
solved this, naming them *(Setup)* and *(Management)*; both copies of the card
in this repo had not. Three things went wrong at once and not one of them
errored:

* `rate_card.find()` walked the list and returned the **first** match, so a
  quote for management was costed against the setup fee;
* the IO builder's `productConfig` is a dict **keyed on the label**, so the
  second row overwrote the first — 90 card rows became **88 products**, and
  neither setup fee could be put on an insertion order at all;
* `check_drift()` is keyed on the label too, in *both* dicts, so the one check
  that exists to notice the two copies disagreeing could not have seen a
  difference between the pair it was collapsing.

The two ends failed in **opposite directions on the same product** — the
proposal quoted the setup fee, the IO could only bill management — which is
the shape that survives review, because each screen is internally consistent.

Both copies carry the published names now. The lookup is the other half:
**a name that could mean more than one product resolves to none of them**, the
`client_key.resolve()` rule wearing a rate, and `candidates()` is what a screen
shows instead of the refusal so it never reads as *not on the card*.
`product_intake.classify()` returns those candidates and commits to neither,
which is what that function already said it did. **A category resolves what a
name cannot** — four headings carry a product called *Behavioral* at four
different rates — so `find(name, category)` and the IO's `cardLabelFor(name,
category)` take one, and a line that recorded its heading is answered rather
than asked. `ai_match` offers the candidates rather than dropping the row: a
model that names an ambiguous product has not invented anything, and dropping
it leaves the rep with nothing.

`test_proposal_spec.py` asserts every label is unique in both copies, that the
IO's product list is the whole card rather than what survived a collision, and
that the names we publish are the names we quote from — the partner pages ship
in this repo, so that last one is checkable rather than remembered.

**And a rename for the reader is not a rename of the join.** The IO template
carries a `PRODUCT_RENAME` that turns `Select Tactics - Comes with Retargeting`
into the friendlier `Programmatic Campaign with Retargeting` — and it ran over
the very array `cardLabelFor()` searches, so after that line **no row answered
to the name the card actually uses**. A proposal quotes the card, so the
lookup returned `""` for the go-to display product: the one
`rate_card.CATEGORY_GOTO` names and every awareness and traffic goal
recommends first.

It failed differently at each of the two doors, and neither errored. An
**uploaded** proposal pushed it onto `unmatched` and dropped the line. A
**converted** one fell back to the raw product name — which is not a
`productConfig` key, since the labels are built after the rename — so the line
reached the insertion order with no rate, no benchmark, no requirements and no
timeline, looking like a product nobody had filled in.

The rename stays, because somebody chose that wording for the screen; what is
kept beside it is `originalProduct`, and `cardLabelFor()` matches both. The
refusals had to survive gaining a second name to match on, so
`test_io_start.py` asserts them in the same breath: a product four headings
share still resolves to none of them, and Google Grant's setup fee is still
not confused with its management fee. It lifts the functions out of the page
and runs them in node rather than restating them, or the test is a second
description of the join.

### A transcription is only as good as the day it was taken

`hub/creative_specs.py` transcribes the S1M CREATIVE SPEC KIT rather than
fetching it, and that is still right: a spec table pulled live changes what a
check says with no diff to point at. What the argument never covered is the
transcription going stale, and it had — in **both** directions, silently,
because the kit and the verdict are each internally consistent on their own.

The kit says in as many words that **"the flat 150 KB rule is gone"**, that
**970x250 is called Billboard** because the IAB retired the Rising Stars
programme, that **SVG is now accepted**, and that **"15 seconds or 3 loops" is
no longer a universal rule**. The code was enforcing every one of the retired
versions. Half Page and 970x250 were judged at 150 KB against a published
250 KB, so the checker **refused files the client had been told to send**; a
smartphone banner was allowed 150 KB against a published 50 KB, the same fault
running the other way; and the mobile interstitial was sized 320x480, which is
on none of the three rows the kit sells it at.

**A target is not a floor.** DOOH publishes "40 KB target / 750 KB max", and
that 40 was carried as `min_bytes` — which `check()` treats as a **fail**. A
clean 30 KB billboard was refused for being too small against a number nobody
published as a minimum. It is `target_bytes` now, and the parser that reads the
page takes the figure labelled *max* rather than the first one it finds,
because taking the first is the same confusion one level up.

**The names changed and the ids did not.** `tags_for()` writes `unit_<id>` onto
every file delivered through the upload manager, so renaming `rising_star`
would orphan the tags already on a year of creative in Cloudinary to correct a
label — `hub/audit.LOG_NAMES`' rule, wearing a spec. The id stays; the name is
what a person reads.

**The page ships in this repo, so the transcription is checkable rather than
remembered.** `kit_drift()` reads the unit tables out of
`hub/partner_pages/creative-specs.html` and compares; `/api/integrity` runs it
at **high**. It stays a *check* rather than becoming the source, for the reason
the transcription exists. And a page it cannot read is **not measured** rather
than no drift: that is the one state where a clean answer would be a lie. Only
the three sections whose table is Unit / Dimensions / weight are read — the
social sections publish prose per format, and a parser guessing at those would
report drift that is not there.

**Tablet display is ours.** The kit publishes no tablet section at all, so
those four units carry `source: "house"` rather than reading as transcribed —
the rule `HOUSE_LEGIBILITY` in `services/abcd_service.py` already works to.

**And the check covered three sections of twenty-three while answering "no
drift".** That is a clean bill of health about seven per cent of the thing it
audits, and the exclusion note explained only half of it: six Meta sections
genuinely publish prose per format, and seven more — native display, YouTube,
the CTV interactive formats, X, LinkedIn, Snapchat and TikTok — publish a
perfectly good *table* whose columns are Format / Copy / Media / File Size
rather than Unit / Dimensions / weight. The blanket reason was applied to all
twenty. The page in this repo is now the **2026** kit and says on itself
*"20 formats updated · 3 added"*, against a transcription taken from 2025, so
a section outside the parser is not a hypothetical gap — and what makes it
dangerous is that it is invisible: a section the *next* rebuild adds is
silently outside every check here for ever, with the panel green. The same
shape as a sweep that quietly stops sweeping.

Every published section is declared now — `_KIT_UNREAD` with the reason its
table cannot be read, and `kit_coverage()` reports one that is not, in **both**
directions: a section on the page nobody declared, and a declaration that
outlives the section it described. `compliance_spec.NOT_ENFORCED` and
`ghl_scopes.NOT_REQUESTED`'s rule, wearing a spec: a thing left out on purpose
is named with its reason, so its absence is never ambiguous between an
oversight and a decision. A page that cannot be read is **not measured**,
never "nothing undeclared" — that is the one state where a clean answer would
be a lie. It started empty, which is the only way it was worth adding.

**And the names were the half that reaches the client fastest.** `kit_drift()`
compares numbers and can only read the three sections whose table is Unit /
Dimensions / weight. The social sections publish a different table — but its
first column is a **format name**, and a name is what the requirement line
prints at the client. **X** is the case that shows the cost: its 2025 model
named eight formats and **not one of them is a format X still sells**. "Website
Card" and "Direct Message Card" are retired, and the two mobile/desktop pairs
modeled a split the kit says in as many words is gone — *"the
mobile-versus-desktop creative split is gone. one asset set serves both."* So a
client was asked to supply four things that do not exist, and two of them
twice, on the line the client document prints. Silent from both ends: every
name was a real format's name once, the sizes were real sizes, and nothing
errored.

X is transcribed against the 2026 kit now — Image Ads, Video Ads, Vertical
Video Ads, Carousel Ads, Conversation Button, Amplify Pre-roll, Spotlight
Takeover and Polls — and the old `text: {final: 256}` went with it, because the
page says media no longer consumes characters. **The ids are kept wherever the
format survives in substance**, the rule `billboard` already follows from when
the IAB retired the Rising Stars name: `tags_for()` has written `unit_<id>`
onto delivered creative and a gallery filters on it. The four with no 2026
equivalent are in `RETIRED_UNITS` rather than deleted — **out of `UNITS`, so
nothing asks a client for one, and still in `BY_ID`, so a row carrying the tag
resolves to a unit that says what replaced it.** Deleting them would orphan the
tag; leaving them in would go on asking.

`kit_name_drift()` is the check, at **high**, and it covers only the channels
declared transcribed against 2026 — `_KIT_NAME_CHECKED`, which is `x`,
`linkedin`, `tiktok`, `snapchat` and `youtube` today. What is still on the
2025 transcription is named in `_KIT_NAMES_PENDING` and carried by
`kit_coverage()`. A backlog named rather than left as an absence — a check
listing every platform on the day it is written is red on the day it is
written, and gets switched off.

**YouTube was the last of the four, and asked for a format that does not
exist.** Google repurposed *TrueView* in October 2025 as a **metric** —
TrueView views, spanning skippable in-stream, in-feed, Shorts and Masthead —
so the requirement line asked a client to supply a thing with no definition.
Shorts was absent entirely and only 16:9 was modelled against a kit selling
16:9, 9:16 and 1:1. And the weight was the half that refused real work: **10
MB against a published 256 GB**, the kit's own *"wrong by four orders of
magnitude"* — the third of the four transcriptions to run that way, after
TikTok's two units and Snapchat's pair.

Six formats now, and **no duration on skippable in-stream at all**: the kit
publishes *"no maximum, under 3:00 recommended"*, and a ceiling invented from
a recommendation refuses a cut the kit permits — the `target_bytes` rule
wearing a stopwatch. `youtube_trueview` keeps its id, because skippable
in-stream is what TrueView was and `tags_for()` has written
`unit_youtube_trueview` onto delivered creative: the rule `billboard` follows
from the IAB retiring the Rising Stars name.

**Native display was that different job, and it is done.** Its first column
is an *asset* rather than a format, and OpenRTB Native 1.2 sets no character
limits at all — each seller declares its own per placement — so the kit
publishes The Trade Desk and Google Demand Gen side by side and says to
**build to the strictest platform in the plan**. That is what each field
carries, with the looser platform named in the notes rather than lost: a
25-character short title because The Trade Desk publishes 25 where Demand Gen
allows 40, and a 150 KB logo because Demand Gen caps there where The Trade
Desk does not.

The 2025 model held two units and a single `headline: (15, 55)` /
`description: (25, 120)` range — which the kit's own update note quotes as the
thing that is wrong, *"character limits are per-platform, not a single 15–55 /
25–120 range"* — so a client was told a 55-character headline was fine on a
platform that takes 25. **Business name** and **call to action** are asset
fields a native ad renders and nothing here had ever asked for, and the HTML5
package the section publishes was absent too.

**And that section is where the kit retires a whole category of ours.** One
sentence under Native Display: *"Tablet Display retired as a category — IAB
removed device-class ad units. 300x250 and 728x90 serve on tablet as the same
units."* Four house units here modelled it, and two of them asked a client a
second time for a file they had already supplied. The third is the one that
showed: **`tablet_interstitial` at 1024x768** was on every display
requirement, because 300x250 and 728x90 dedupe against their desktop twins in
the size run and 1024x768 does not — an extra file, for a placement nobody
sells inventory for. All four are in `RETIRED_UNITS`, and the **channel is
unwired from the product map as well as emptied**: named there with no unit
behind it, `required_units()` reports *"the spec kit maps no unit for this"* —
a warning about our own dangling entry, printed at the client.

**A third state, because two would have been a lie either way.** Native
display is transcribed against 2026 and still cannot join `kit_name_drift()`:
four of its eight rows are character limits carried on the main image rather
than units, and the HTML5 package sits under its own heading outside the table
the parser reads, so the name pass would report our own unit as a format the
kit does not sell. Left in `_KIT_NAMES_PENDING` it would claim a 2025
transcription that is no longer there; added to `_KIT_NAME_CHECKED` it would
report a finding that is not one. `_KIT_NAMES_UNCHECKABLE` is the third
answer, with the reason, and `kit_coverage()` carries all three.

**And the branch that answers when the page cannot be read was missing
them.** `kit_coverage()`'s not-measured return carried no `names_*` keys at
all, so a caller reading one — `test_proposal_spec.py` does — would raise on
the one day the check exists for, rather than reporting that nothing was
measured. Both branches answer with the same keys now, asserted.

**And a codec list is a ceiling too.** That transcription carried five of the
nine formats the kit publishes — *"MPG (MPEG-2 / MPEG-4) preferred, plus MOV,
MP4, WEBM, ProRes, DNxHR, CineForm, HEVC"* — so a **ProRes master, which is
what a finishing house hands over**, was still refused by the checker. The
same shape as the 10 MB ceiling it had just replaced, one field along, and
invisible for the same reason: five real formats look like a complete list.
`_YOUTUBE_FORMATS` is named once, because every YouTube unit takes the same
nine and two hand-typed copies is how one of them comes to be missing HEVC.

**A run of nine codecs is the wall the sizes rule already exists for.**
Printed once per unit across a six-unit buy, on the line a client reads, it
buries everything else on it. `_describe_unit()` prints five whole — which is
every other unit in the kit — and past that says how many more, rather than
pretending the list is all of them.

**What did not move is the rate card.** It sells products called *TrueView*
and *TrueView - Targeted* — product names on an invoice rather than format
names in a creative requirement. Renaming one orphans every quote, every IO's
`productConfig` key and the published partner page, which is the migration
this codebase refuses to do casually.

**And naming the formats exposed the line that had been dissolving them.**
`units_line()` folds image units into one run of sizes, which is right for a
display buy — "Leaderboard" *is* 728x90, and eleven labels beside eleven sizes
is the wall its own comment describes. It is wrong wherever the kit's first
column is a **Format**, and it had been wrong on every such channel: an X buy
asked for **nine bare sizes** with Image Ads, Carousel Ads, Conversation
Button and Spotlight Takeover all dissolved into them; LinkedIn the same
across six; and native display printed *"1200x628, 200x200"* with nothing
saying which of the two is the brand logo. That is `_shape_of()`'s own note
running the other way — there a unit reaches the line as a bare name, here as
bare sizes with the name gone, and on a format-name channel the name is the
entire ask.

The discriminator is the published page's own structure rather than a
judgment. `SIZE_SET_CHANNELS` is derived from `_KIT_SECTIONS` — the three
sections whose table is Unit / Dimensions / weight, the same three
`kit_drift()` can read — plus `tablet_display`, which is ours and is the same
shape. Everywhere else the name leads and its sizes ride with it. Nothing
about display, DOOH, email, CTV or Meta changed, and **both `ADDITIONS`
entries are decided before the split** — the radio companion and Snapchat's
AR filter each sit on a channel that sells no size set, so filtering by
channel first would have retired the one rule that keeps *"plus a companion
banner: 300x250"* from reading as the whole requirement.
`test_proposal_spec.py` asserts both directions, and every new check was
confirmed red against the real defect first.

**And a name check cannot see a number, which is how LinkedIn was refusing
files the kit told the client to send.** Its 2025 model held five formats to
the kit's eleven and `Sponsored InMail` named a category LinkedIn has split
into *Message Ads* and *Conversation Ads* — the X failure, found the same way.
What the name pass could not reach is three ceilings that had each moved
**upward**: Message Ads at 40 KB against a published **2 MB**, Sponsored
Content video at 200 MB against **500 MB**, and that video carrying a
`max_width` of 1080 while the kit publishes 1920. A ceiling that is too low
fails in the direction nobody checks — the upload manager refuses a file that
is *correct*, the client is told to send it again smaller, and every screen
reads as working. That is the Half Page failure `kit_drift()` exists for, and
`kit_drift()` cannot see this one either: it reads the three Unit / Dimensions
/ weight sections and LinkedIn's table is Format / Intro / Headline / Media /
File size. Only transcribing it finds these, which is the whole argument for
working the `_KIT_NAMES_PENDING` list down rather than waiting for a check to
raise its hand.

**A size the kit publishes exactly is a size we judge exactly.** The old model
carried `1200x627` and the kit publishes `1200x628`, so that one pixel now
fails — named, with the four accepted sizes in the refusal, rather than
absorbed by a tolerance. Inventing a ±1 would be house guidance wearing the
kit's name, the thing `HOUSE_LEGIBILITY` is kept out of `THRESHOLDS` to avoid;
1200x628 is what the client is asked for on the requirement line, so it is
what the file is held to. **Six formats are new** — Document, Thought Leader,
Event, Connected TV, Click to Message and Conversation Ads — and two of them
have **no file to judge at all**: a Thought Leader ad runs an author's own post
and an Event ad pulls its 4:1 image off the LinkedIn Event page. Those are
modeled as `kind: "other"` with no ceilings rather than left out, so a
requirement can name them and `check()` is never handed one — the answer
`x_polls` already gives.

**And TikTok is the same finding with the cost the other way up.** Its 2025
model named three formats to the kit's six and not one of the three was a
format TikTok sells — found by the name pass, as X and LinkedIn were. What the
names could not reach is that **two of the three refused creative the kit
allows, and the third asked for a file that no longer exists.** The in-feed
video was capped at **:60** against a published **10 minutes** and took two
file types where the kit takes five; the image ad was pinned to **1200x628 at
500 KB**, when the kit specs images by ratio now and says in as many words that
1200x628 *"survives only as the horizontal carousel option"* — so a 720x1280
vertical, the shape TikTok itself recommends, was refused outright. That is the
LinkedIn ceiling failure one channel over, and the second time in two
transcriptions that the numbers were worse than the names.

**A format the kit stops selling is retired, never re-pointed.** `tiktok_image`
and `tiktok_profile` are in `RETIRED_UNITS` — out of `UNITS`, so nothing asks a
client for them, and still in `BY_ID`, so a row carrying `unit_tiktok_image`
resolves to a unit saying what replaced it. Quietly aiming that id at the
carousel instead would make a delivered 1200x628 read as one card of a
two-to-thirty-five image set, which is a wrong answer wearing a fix. Profile
Image goes for a reason that is not about pixels at all: Custom Identity is
being retired, so from January 2026 the avatar is **inherited from the linked
TikTok account** and there is nothing for a client to supply. `tiktok_video`
keeps its id through its rename, the `billboard` rule.

**A target is not a ceiling, and the carousel is where that bites next.** The
kit publishes *"100 KB suggested per image"*, which is `target_bytes` and not
`max_bytes` — carried as `min_bytes` once already, in DOOH, where a clean 30 KB
billboard was refused against a number nobody published as a minimum. Read as a
maximum here it would refuse a 140 KB card the kit is perfectly happy with.

**And Snapchat is the case where the names were right all along.** It is the
one platform of the four whose two format names the kit still sells, so the
name pass would never have raised it and `_KIT_NAMES_PENDING` recorded it as a
count — *seven formats against our two*. Both of the two were nonetheless
refusing creative the kit allows, which is the third transcription in a row
where the numbers were worse than the names and the whole argument for working
the list down rather than waiting for a check to raise its hand. Video was
capped at **:30** against a published **:03 to 3:00** — the kit's own update
note says *"the 30-second cap is gone"* — so a :45 spot was refused outright.

**One fact, three numbers, and collapsing them is what refused the file.** The
kit publishes *"9:16, 1080x1920"* as the media spec and says beside it that
**720x1280 is the stated minimum, not the target**. Carried as one fixed
`size`, a perfectly legal 720x1280 file failed on dimensions. It is the
`gpt_ads_square` rule: a **required** ratio is a `ratios` entry and a fail, a
**recommended** build size is `min_size` and a warn — it runs, it just runs
soft — and the floor is `min_width` and a fail under it. Three answers,
because there are three questions.

**A unit specified by ratio still has to say what it is.** `units_line()`
already knew an image unit with no size of its own must be *named* rather than
folded into the run of sizes, because folded in it vanishes. Named alone it
reached the client document as **"or Single Image Ads"** — nothing saying
9:16, nothing saying 1080x1920, nothing saying JPG — which is the same silence
one step less complete. `_shape_of()` is what a unit carries when it has no
fixed size, and every social unit is in that position.

**And an optional extra must not lead.** The companion rule was keyed on
`radio_companion` deliberately, after firing on a *count* once and announcing
Snapchat's and TikTok's primary images as optional companions. There are two
of them now: an AR filter is the same shape as the radio companion — a sized
extra beside a buy whose ask is a 9:16 spot — and being the only sized image
unit there, it led the requirement, announcing an AR filter as the whole of
what the client owed us. `ADDITIONS` maps each to **its own words**, because
"a companion banner" is a true sentence about one of them and not the other.

**AR Filters stays one unit carrying two shapes** — a static 945x2048 PNG and
a moving 720x1560 GIF. Splitting it would invent two names the kit does not
publish, which is exactly what `kit_name_drift()` exists to catch. No file
weight is published for it, so none is invented.

**Three of the twenty were a different kind of gap, and it reached the client
document.** Instagram Reels, Facebook Reels and the six CTV interactive
formats are sold by the kit and this module held **no unit** for any of them
— not "we cannot parse that table" but "there is nothing here to judge one
against". So a Meta requirement listed Stories and never Reels and read as
complete, on the page the client is sent, while the kit itself says in as many
words that *"Facebook Reels and Instagram Reels are not interchangeable —
different file types, text limits and duration rules."* That is the Pinterest
failure one placement along: judged against the nearest thing rather than
reported as not measured. `_KIT_NOT_MODELLED` named them against the channels
whose presence put them in play, and `required_units()` carried them in the
payload **and** on the one line `units_line()` prints — left in the note
alone, the requirement a client actually reads still looks complete.

**All nine are modelled now, and `_KIT_NOT_MODELLED` is empty.** The kit's own
sentence is the specification: the two Reels units differ in **three** ways
and a single shared unit would have satisfied every check above and been
wrong — Instagram refuses GIF where Facebook takes it, Instagram caps at
fifteen minutes where Facebook publishes none, and Facebook carries a
55-character headline where Instagram has no such field. Both are on
`_META_CHANNELS`, so a Meta buy asks for them by name.

**The six CTV formats are modelled and are deliberately not an ask.** Pause,
menu, screensaver, in-scene, squeezeback and overlay sit on a channel of their
own — `ctv_interactive`, which no product maps to — because the kit sells them
*beyond* the standard in-stream spot and says in as many words that
availability varies by publisher. On the `ctv` channel they would have joined
every CTV requirement line, asking every client for six files against a
portfolio their publisher may not carry: the crying-wolf failure `ADDITIONS`
and `QR_CODE_RULES` both refuse, on the line a client reads. Modelled means
`check()` can judge one that arrives and `BY_ID` resolves its tag; it does not
mean anybody is asked for it, and each carries the kit's own
confirm-before-selling warning in its notes.

**All three sections are declared unreadable with their reasons**, because
what the parser cannot read has not changed: the Reels tables publish a format
name against prose rather than the Unit / Dimensions / weight columns
`kit_drift()` reads, and the CTV section publishes minimums rather than
specifications. Declared rather than silently outside every check —
`kit_coverage()` reports a section on the page nobody declared and a
declaration that outlives its section, in both directions.

### A category heading is not a word about the product

`creative_needs.medium_of()` read a blob of `category + product + label +
description`, and the **label** is `"<category heading> — <product>"`. A
heading describes a *section of the card*, not the thing in it. The card files
four IP-targeting products under a heading called **"Display & Video"**, so
the word *video* appeared in the label of `IP Targeted Display - New Movers` —
whose own description reads "deliver **display** ads" — and the video test
runs before the display one. All three IP display products were gated as
video, which asks a client for a TV spot to run a banner buy. The label is out
of the blob; category and product were both in it already, so it contributed
nothing else.

**And "other" is not a medium — it is the gate never being asked.** Every
product under `MOBILE ONLY`, `EMAIL MARKETING` and `SMART 1 SIGNAGE` answered
`OTHER`, and an ungated medium is one the Creative step never mentions. A
signage buy reached the insertion order with nobody having established that
artwork exists, exactly as a CTV buy used to before the gate existed. Those
three headings are in `CATEGORY_MEDIUM` now and `EMAIL` and `DOOH` are gated
mediums with their own production figures — email creative is the card's own
$150 line, so it is questioned at $400 rather than $1,500, the per-medium rule
display already followed.

**And paid social was the largest hole of the three.** A Meta-only plan
returned *nothing* from `gated_media()`: six real buys — Awareness, Targeted,
Programmatic Paid Social, Retargeting, Leads and Boosted Posts — each with
three to seven units published in the kit, and the Creative step never
mentioned one of them. The tempting reading is that paid social is usually a
boosted post the client already has, and that is exactly the assumption this
module exists to stop making. `SOCIAL` is gated now, at the card's own
"Social Media Ad Creation per platform" of **$35** rather than a figure
invented here — low enough that its comp confirmation is in practice never
raised, which is the right outcome for a $35 line rather than a threshold
nobody would act on.

**And gating it made the word "social" decisive, which caught two things that
are not media buys.** `Social Media Ad Creation per platform` is the card's own
$35 **production** line, so the gate asked whether the client already had the
creative that line exists to produce; and `Social Media Management` is a
$199/month organic posting retainer that buys no advertising at all. Both then
printed *"the spec kit maps no unit for this"* onto the client's creative
section, and both counted their spend into the **social medium** — which is the
figure the comp confirmation is measured against, so a production line sitting
beside a Meta buy raised the number that decides whether comping it is
questioned.

They are named by **category** rather than by product, and that is the point:
the other four lines under CREATIVE / DESIGN SERVICES answer OTHER only because
they happen to contain no medium keyword, so the next production line added
there would depend on that luck. It is also already written down —
`SPEC_AGREE_EXEMPT` carries `("other", "email")` with the reason *a
creative-production line item is not a media buy that needs creative supplied
for it* — so this is that exemption's rule applied one heading up rather than a
new judgment. `spec_disagreements()` cannot see any of it: the kit maps no unit
for either product, and an empty kit is skipped.

**One product runs the other way.** The card files
`LinkedIn - Display & Text Ads` under a heading called **SOCIAL ADS - VIDEO**,
and the heading is what the keyword pass reads — so a product whose own name
says *Display & Text Ads* was gated as video, asking a client for a spot to
run text ads. It is named in `EXPLICIT_MEDIUM`, where `card_drift()` will
report it if the card renames it. The other five under that heading are left
alone: the heading is right about them, and reclassifying a generic "Paid
Social Media Advertising" on our own reading of which platforms are
video-first would be inventing.

**And the heading was answering for a platform the kit has never heard of.**
The same reading one step further on: `channels_for_product()` matches on the
category as well as the product, and **Pinterest** is on the card with no name
rule of its own — so "SOCIAL ADS - VIDEO" hit the `social ads?` pattern and a
Pinterest buy was asked for **Facebook and Instagram units**. Not a near miss.
The kit publishes no Pinterest section at all, Pinterest's feed is 2:3 and what
was asked for is a 1:1 square and a 9:16 story, so a client who supplied
*exactly what the requirement listed* delivers creative Pinterest crops — and
nothing errors at either end, because the sizes are real sizes and the request
looks like every other social requirement on the screen. Snapchat, TikTok, X
and LinkedIn each carry a name rule above that pattern and were right all
along; Pinterest was the one platform the category was answering for, which is
why four of the five looked like proof the reading worked.

**And the busiest social family on the card was taking the narrower answer.**
The `instagram` rule sits above the Facebook one and returns a deliberately
narrower list, because it was written for a product named only Instagram —
and every Meta product on this card is called **"Facebook | Instagram …"**, so
five of the seven took it and were asked for an Instagram image and a Story
and never for the Facebook feed, the Facebook video or the carousel. On the
one whose own name says *Video* that is worse than it sounds: `facebook_video`
was dropped from a video buy. Nothing errors — every unit returned is a real
Meta unit, just not all of the ones being bought — and the two products named
"Facebook - …" got the full set the whole time, which is why it read as
working. A product naming **both** platforms now gets the whole set, above
both rules, and `_META_CHANNELS` is written once: two hand-typed copies of one
list is how one of them comes to be missing the carousel.

**An image unit with no size of its own vanished from the requirement.**
`units_line()` folds image units into a run of sizes and describes everything
else, so a unit carrying no size contributed nothing at all and was silently
absent. Every social unit is in that position — the kit publishes a ratio and
a recommended resolution for those rather than a fixed size — so a paid social
buy's entire creative requirement, the one line a rep and the client document
read, said **"Stories Video (MP4/MOV, 0–120s)"**: four image units gone, and
nothing anywhere saying an image was needed. That is this function's own audio
rule running the other way — there a unit is described in the wrong terms,
here in none — and it went live the day the paid social gate did, which is
what made it worth finding. TikTok's Profile Image and two of X's units were
disappearing the same way.

**And "plus a companion banner" is a claim about one unit, fired on a count.**
It belongs to digital radio's optional 300x250 — named first it reads as the
whole requirement, which is how somebody sends a banner and no audio — and it
was triggered by *one sized image plus anything described*. So Snapchat's
Single Image Ad and TikTok's In-Feed Image, which are the primary image of
those buys, were each announced to the client as an optional companion to the
video: the same sentence costing the image instead of the spot. It is keyed on
the unit now.

It maps to **nothing** now, and that is the fix rather than a guess at the
nearest platform: `required_units()` already says *the spec kit maps no unit
for Pinterest* when it is handed an empty list, which is the rule this module
works to everywhere else — a format the kit maps no unit for is *not measured*,
never judged against the nearest channel. The gate is unchanged and still asks
whether the creative exists; only the claim about **what** it has to be is
withdrawn. `spec_disagreements()` skips an empty kit rather than reporting one,
so this is silent to the check that would otherwise have caught it — which is
why `test_proposal_spec.py` asserts it directly, and asserts the other four
platforms still reach their own units: the tempting edit is to widen the entry
to cover "the social ones", and that would take Snapchat's and TikTok's real
sizes away to fix Pinterest's absent ones.

**Two readings of one question, disagreeing in both directions.**
`medium_of()` decides *whether* to ask for creative;
`creative_specs.channels_for_product()` decides *what* to ask for. They
disagreed on **25 of the 90 products**. The kit had known about mobile, email
and signage the whole time. Going the other way, the four programmatic
**video** products filed under the card's DISPLAY heading are named in
`EXPLICIT_MEDIUM` so the gate asks for a spot — and the kit's regex list
dropped them into the generic `display|programmatic` pattern, so the rep was
asked for a video and handed *Leaderboard 728x90, Medium Rectangle 300x250*.
Each screen was internally consistent, which is why it survived.

`spec_disagreements()` is the check, and `/api/integrity` runs it at **high**.
Its exemption list is pairs that are genuinely both right, each with its
reason: a retargeting *buy* whose files are banners or Instagram units, and a
creative-production line item that is not a media buy needing creative
supplied for it. The new video pattern in `_PRODUCT_CHANNELS` sits **below**
the audio rule on purpose — "Programmatic - Targeted" is also the $18.00 CPM
buy under DIGITAL RADIO, and that one needs a spot rather than a video.

The tables are deliberately **not** merged. The wizard mirrors `medium_of` in
JavaScript, and reaching into the kit's twenty-entry regex list would be a
third mirror of one fact — the cost this codebase has already paid twice. They
stay separate and are held together by the check. `test_proposal_spec.py` runs
the mirror over **every product on the real card** rather than a fixture: a
hand-written list proves the halves agree about the rows somebody thought to
write down, which is exactly the set that was already right — none of the four
the label bleed broke were in it.

### The listed rate is what Smart 1 pays; the quoted rate is what is sold

Every rate on the card is the buy-side number and the builder was quoting it
straight through — so a proposal promised the client the card's own CPM and
the delivery table computed impressions **at cost**, with no margin anywhere
in the document and nothing saying one was missing. A $1,000 line at $4.25
promised 235,000 impressions on the client's own ROI table and could only ever
have bought half of them.

`rate_card.sell_rate()` starts every CPM and CPV line at **2×**, editable per
line, and `_sell_rate()` in the builder is the one server-side reading of what
a line is quoted at — the delivery table, the packages and the media plan all
go through it. A management-fee, flat-fee or custom-quote line has `rate_type`
of None, nothing to multiply, and is left exactly as the card lists it: that
is what "not managed by percentage" means in practice. The line's own rate
also wins over the looked-up card row, because `find()` matches on the product
name and four categories carry a product called "Demographic" — a location
lookback line resolved to the $4.25 display row and was costed against a rate
nobody quoted.

**And a rate with no `rate_type` is not a rate that is sold.** All four IP
Targeting products carried a bare `listedRate` of `"25.0"` with `rateType:
null`, in both copies of the card, while the page we publish sells every one
of them per **CPM** — `$18.00`, `$25.00`, `$19.50`, `$31.00`. Two things fell
out of that, in opposite directions, and each screen stayed internally
consistent, which is why it stood. `sell_rate()` answers `None` for a line
with no rate type, so the **buy-side rate went onto the proposal with no
margin on it**, beside a display line correctly doubling $4.25 to $8.50. And
`estimate_delivery()` answered *"25.0 — not an impression-based rate, so
delivery isn't estimated here"* about a $25 CPM buy, so the media plan quoted
IP Targeting with **no impressions** and printed the bare float at the client.

Nothing here could see it. `check_drift()` holds the two copies of the card to
each other and they agreed — both were wrong the same way. The only place the
unit exists is the published page, which ships in this repo, so
`test_proposal_spec.py` now holds the card to it: a product the page sells per
CPM or CPV that the Hub does not mark as one is a failure. It **also asserts
how much of the card matched**, because the names do not all join — the Hub
spells one product "purchased seperately" and the page spells it "separately"
— and a name comparison that quietly stops matching is a check reporting a
clean bill of health about nothing.

The five genuine flat fees beside them keep `rateType: null`, which is what
that means, and gained the rate string the page prints: `250.0` reaching a
client document as *"250.0 — not an impression-based rate"* is the same bare
float one row over. An existing quote is unaffected either way — a saved line
carries its own rate, which is the rule `_sell_rate()` already works to.

### What a goal leads with, and what a client reads it as

`findProduct(category)` meant "the first row the card happens to list under
that heading", and the card's order is the order somebody typed it in. Three
wrong answers came out of that, each of which reads to a client as a
deliberate recommendation.

**Run of Network led DISPLAY.** RON is $3.50 CPM of untargeted inventory — a
volume top-up to a targeted buy — and it was the display product every
awareness and traffic goal recommended first, on a document arguing that
Smart 1 targets precisely. `ADD_ON_ONLY` and `CATEGORY_GOTO` are data in
`hub/rate_card.py` now: programmatic (DATA TARGETED DISPLAY, "Select Tactics",
which builds the custom audience and carries retargeting with it) is the
go-to, and RON stays addable by name and is never chosen for anybody.

**"Demographic" led LOCATION LOOKBACK.** Four categories carry a product
literally called "Demographic" or "Behavioral", so the quote line said
*Demographic* where the tactic sold was location lookback — a client reading
it cannot tell which of the four they bought, and neither can the IO.
`quote_label()` puts the category in front of the ambiguous names and leaves
the self-describing ones alone: "Connected TV - Targeted" is not improved by
having OTT bolted to the front of it.

**"Social Ads" was the whole of paid social, next to Meta.** That category is
Facebook and Instagram *video*, LinkedIn, TikTok and Pinterest, so it is
**SOCIAL ADS - VIDEO** on all three copies of the card. `check_drift()` still
reports the shared card and the IO template agree.

Both of the first two are data rather than rules in the wizard, because the IO
reads the same card and the two documents must not disagree about what was
sold.

### The Suite is an option, and it says what it is for

It was quoted on every proposal at a tier picked purely from media spend, with
the client never told which of the things they said they were not doing it
closes — so the one line on the Investment Summary that recurs for ever had no
stated reason for being there. It can be left off the quote, its price
adjusted with the reason recorded **internally** (a discount nobody recorded
is a discount nobody can renew, and it is not the client's business), and
`current_marketing.suite_coverage()` answers three ways, never two: what this
tier covers, what a **higher tier** would (named with the tier — offering
smart webchat against a Smart 1 licence sells something the client cannot
switch on), and *not measured*, because an unanswered discovery question is
not a gap the Suite gets credit for closing. A capability is claimed **once**
however many questions want it: both social questions are answered by the
social planner, and listed separately they read as two things the licence
buys, on the one panel whose job is to justify a recurring charge.

### Every discovery answer changes something, and `unanswered_keys()` proves it

`socialPosting` had no suggestion rule behind it from the day it was written,
so a client who posts nothing produced a proposal that never mentioned it —
the exact failure `hub/current_marketing.py` was written to undo, sitting
inside the module that undid it. Social post *scheduling* was never asked at
all. Both exist now, and `unanswered_keys()` names any question with neither a
suggestion rule nor a Suite feature behind it. It returns an empty list today,
which is the only way it was worth adding.

The suggestions also reach the screen where products are **chosen**. They were
raised on the discovery step, three steps earlier, in a panel a rep reads once
and walks away from — so the media mix was built from the goals alone and the
answers were, in practice, read by the proposal copy and by nothing that
decides what is on the plan. Nothing is added automatically: a plan that grows
a line by itself is a plan somebody has to audit before every send.

### A ZIP exception is a rule, not a note

A radius does not stop at a state line and a campaign frequently does — a
client licensed in one state, a franchise with a protected territory, a dealer
whose registration only works one side of the river. The restriction lived in
an email, and the only two outcomes were a rep deleting a hundred ZIPs by hand
or the list shipping as it came back. Both are silent: nothing in either
document said the list was supposed to be narrower, so the proposal quoted
reach the client could not use and the IO trafficked into a state nobody was
allowed to sell in.

`target_areas.parse_zip_rule()` reads it the way it is said ("only New Jersey
zip codes", "exclude 46032, 46033", "everything except Ohio") against a table
of USPS **prefixes** — the first three digits are the sectional centre and
never straddle a state line, so a five-digit range table would need
maintaining every time a facility opens. Three rules on it:

- **A rule nobody could read is never silently ignored.** It comes back
  `understood: False` with the text kept, and every screen — and the client
  document — says *not applied* beside the sentence. A restriction that reads
  as saved and does nothing is worse than one nobody typed.
- **Filtering only ever removes.** "Only New Jersey" on a radius touching no
  New Jersey ZIP leaves nothing, and that empty result is reported as an empty
  result rather than falling back to the unfiltered list.
- **The found list is kept.** Loosening a rule, or fixing a typo in it, must
  not mean running the radius lookup again — that call is billed, slow, and
  the second answer would differ from the first.

`all_zips()` and `to_legacy_geo()` return the **running** list, because those
are what the IO's ZIP field and the Suite webhook read: a rule that narrowed
the proposal and not the insertion order would leave the document a client
signed and the campaign that was trafficked disagreeing, with both looking
correct on their own. The exception is part of an area's identity in the
dedupe key too — the New Jersey half of a Philadelphia buy and the
Pennsylvania half are two areas, and without that the second collapses into
the first.

The parse is a **round trip**, not a fourth JavaScript mirror. Target areas
and the creative classifier each carry one already and each needs its own test
proving the halves agree; a third, carrying every state's ZIP prefixes, would
drift silently and be wrong about which state a campaign runs in. The browser
stores what comes back on the area, so every later read is local.

### Competitors are named, age and income are a range

The audience step offered "Competitor physical locations" and "Competitor
conquesting" as two chips, and a ticked chip is not a campaign: nothing
anywhere asked *which* competitors, or which addresses. So the proposal
promised to target a client's rivals without naming one, the IO arrived with
the same two words on it, and the first person who had to know — whoever
builds the geo-fence — went back to the rep weeks later. Meanwhile the client,
the only person in the room who knows who they lose business to, was never
asked. A row with **no address is still kept**: conquesting by brand and
browsing behaviour needs no location, and refusing the row until somebody
looks up a street address is how the list stays empty. Nothing is inferred
from a name — no guessed website, no geocoding — the rule
`modules/ads_builder/logo.py` works to.

Age and household income were thirteen tick boxes, so "25-34 and 55-64 but not
the fifteen years between them" was two clicks away and reached the IO looking
deliberate, while a low and a high took five. And nothing said what *no chips
ticked* meant: the reach estimate read it as everybody and the IO read it as
nothing. Both are a low, a high and an explicit **none** now — none being a
real answer, and not the same as a range covering everybody, which is a
decision to buy the whole distribution and is priced as one. The stops are the
buckets the estimate is actually built on rather than arbitrary years: a
slider offering 37 implies we can size 37. The bucket lists are still written
alongside, because the IO, the Suite webhook and `/api/estimate-audience` all
read those.

### A form bound to a throwaway object is a form that reports nothing

Three reports came off the floor about "Where should we run this?" on one
afternoon, and two of them were one bug. **Pressing Back off a half-typed
target area emptied the list while `S._areaView` still said `"edit"`** — the
one state the step's own seeding skipped, because it seeded only when the view
was *not* edit. The editor renders `currentArea() || blankArea()`, so from then
on every control on the step was bound to a throwaway object: picking DMA set a
property on it, the redraw read the list, and the select snapped back to
City/ZIP + Radius with the radius at 10. Nothing threw. No console error, no
failed request, no toast — the step simply could not be filled in, and
`_areaView` rides in the saved quote, so it stayed dead across reloads and for
every later visit.

Two halves, because either alone leaves a route in. The step seeds **whenever
the list is empty**, whatever the view says; and `setArea()` **seeds rather
than returning** when there is no current area, so no route into that state can
leave the controls writing into nothing. `onBack` also clears the view it can
no longer honor: leaving `"edit"` standing over an empty list is what got
written into the quote. `test_proposal_targeting.py` runs the step's own source
in node — the seeding block and the `onBack` body are lifted out of the page
rather than restated — and asserts that picking DMA sticks.

### A model cannot visit a page, and asking it to is how it says so

The landing-page review handed a URL to a model with the word "Visit". No model
here can, so the answer was either a confident review of a page nobody had
looked at — fiction, quoted to a client — or, once the model was honest about
it, the criteria it *would* have used followed by a sentence about not being
able to reach the site. That is what the third report described. It is the
failure `modules/ads_builder/landing_page.py` was written to undo, in the
module next door: it **fetches** the page and counts the conversion points off
the markup, each carrying the evidence found. It is read here rather than
copied, and the response carries `observed` beside `review` so the screen keeps
the fact and the judgment apart. A page that could not be fetched is **refused
rather than reviewed anyway**, and a model that fails costs the judgment and
not the reading.

**And the IO Builder was asking the same model to visit the same kind of
page**, into a worse document. Its review is printed on the internal PDF under
*Landing Page Review — Internal Needs*, which is what whoever traffics the
campaign reads, so a review of a page nobody looked at is a fix list somebody
works. It reads `landing_page.observe()` too now, keeps `observed` and
`summary` beside the review, refuses a page it could not fetch rather than
reviewing it anyway, and refuses an empty review rather than filing one. The
headings line the prompt needs moved to `landing_page.headings_line()` — it
describes that function's own output, and two readings of one shape drift the
day either end of it changes.

### The hosted tool that was meant to help is what stopped the button

`_openai_response` attached `{"type": "web_search"}` to **every** call in the
Proposal Builder, including the rewrites and JSON drafts that have nothing to
look up. Whether a hosted tool is available depends on the model, and the model
is `OPENAI_MODEL` — set to a 4o-class model on this deployment, not the
`gpt-5-mini` the default in that function assumes. A model that refuses the
tool refuses the whole request, so the search that was meant to help the ZIP
lookup was what stopped it, and stopped seven other buttons with it.

It is opt-in now, and the one caller that asks for it **falls back without it**
rather than losing the answer: a list assembled with no live lookup is worth
having and is labelled; no list at all is a button that does nothing. Two other
ways of failing are named rather than guessed at. `raise_for_status()` was
discarding the API's own sentence, so every button reported a different
invented diagnosis of one shared failure — "the AI returned no description",
"No ZIP Codes were returned" — and none of them was checkable; the body carries
the reason now. And an **incomplete** response is said to be that: reasoning and
tool tokens count against `max_output_tokens`, so a truncated answer arrives
with an empty text body, which every caller read as its own kind of nothing.

**And the fix landed in one of the two copies.** The IO Builder had a
`_openai_response` of its own — same function, same unconditional
`{"type": "web_search"}`, same `gpt-5-mini` default — and was never touched, so
the identical diagnosis stayed live one module over for as long as it took
somebody to press a button there. All four of that tool's AI buttons were dead:
the ZIP-radius lookup, the business description, the landing-page review and
the media-mix recommendation, each returning a different invented account of one
shared cause. That is the drift `hub/storage.py` and `hub/images.py` exist to
stop, wearing a model call, and it is the whole argument for the opportunistic
migration rule: the next fix should land once. `hub/openai_responses.py` is the
one reader now and both builders read it — with the transport left as each
module's own name, so a test already standing in front of it goes on biting.

**Two of those four reported a truncated answer as a success**, which is worse
than the two that got it wrong loudly. The landing-page review answered **200
with an empty review**, and the wizard stored it and the internal PDF printed it
under a heading — a page nobody had anything to say about, rather than a review
that never happened. The media mix answered **200** with every field blank,
under a warning blaming the model for having replied in prose. Neither errored
at either end. An empty answer is refused by name at both call sites now, and
`purpose` travels with each call: every model call in that module was filed
under the string `"business_description"`, so the usage page could not tell a
billed ZIP lookup from a billed landing-page review.

### Generated copy is cleaned, not trusted

The models write Markdown by habit and nothing downstream renders it: the
preview HTML-escapes the body, the PDF escapes it into a reportlab Paragraph,
and python-docx writes it as literal text — so `**Reach**` reached the client
as `**Reach**`, three ways, on a document quoting five figures. Emoji arrived
the same way, in a proposal. This is the Smart 1 Labs rule one step on: the
instruction is in the prompt **and** `proposal_spec.clean_ai_text()` runs over
whatever comes back, and over anything typed into the section editor by hand —
or the rule holds only until somebody pastes. Copy is *cleaned* rather than
discarded, because a section is thrown away for naming Smart 1 Labs and a
stray asterisk is not that. Bold survives, normalised to `<b>`, which
reportlab reads natively, `rich_runs()` turns into a bold run for Word, and
the preview un-escapes deliberately and alone.

### The proposal a client can answer, and a count that means what it says

A proposal reached a client as a PDF and stopped there. Nothing knew whether
it had been opened, and the status was a pill a rep clicked from memory —
`sales_builder` declared no `PUBLIC_PREFIXES` at all, so unlike Scans, Smart 1
Ads, Calculators and Social, none of it was reachable by a client.
`/sales/builder/p/<token>` is the document they open, and the one thing on it
is **accept**.

**No edits from the client, deliberately.** Smart 1 Ads offers three answers
because an estimate is negotiated line by line; a proposal is a document
somebody says yes to, and a change request arriving here would be a second
inbox for a conversation the rep is already having. A client who wants
something different says so, the rep edits the quote, and the same link shows
the new version.

**The page embeds the PDF rather than re-rendering the proposal in HTML.**
That is why it is cheap: the PDF, the Word export and the preview are already
three renderers of one document, and a fourth would be the drift this codebase
has paid for twice. The client reads exactly what was signed off, and there is
nothing to keep in step.

**"Opened 3 times" is a sentence a rep acts on**, so `hub/view_tracking.py`
holds what an open is. Four ways that number quietly comes to mean something
else, and each is closed:

- **A mail security gateway opens every link in the message.** Mimecast,
  Proofpoint and the rest fetch a URL within seconds of delivery, before any
  human sees it — so an open is recorded by the **page reporting itself**,
  which a scanner fetching HTML never does because it runs no JavaScript.
  Counted on the request instead, every proposal reads as opened the moment it
  is sent: a confident wrong answer that stops somebody chasing a client who
  has never seen it.
- **The rep opens it to check the link works.** A signed-in Hub session is
  never counted, and the client page *says so on itself* rather than leaving
  the rep to trust it. This is the rule the feature was asked for with, and it
  is the one that would break silently — the count would simply be one too
  high, with no way to tell which one.
- **A reload is not a second read**, so a visitor is collapsed inside a
  thirty-minute window — **per revision**, because the first sight of a
  version the rep has just sent is a new read whatever the clock says. That
  scoping was a defect the test caught: a client opening a revision five
  minutes after it was sent counted as nothing.
- **Nothing stores an address.** `visitor_hash` is a keyed digest used for the
  window check and for nothing else; the panel shows counts, times and whether
  it was a phone. The rule `hub/auth.py` already applies to its lockout table.

**An acceptance is a statement about one revision**, so it is a row rather
than a flag. A quote revised after a client said yes does not carry that yes
forward onto a document nobody agreed to — the panel says revision 1 was
accepted and is now superseded, and the client can accept the new one. The
share token is minted once and kept for the same reason a revision does not
mint a new one: a link is already in somebody's inbox.

Three smaller rules it inherits. A rep cannot press Accept on the client's
page — an acceptance filed in a client's name that the client never gave is
worse than none. A name and an email are required, because an acceptance
nobody can attribute is not one. And **revoked, deleted and never-existed all
answer the same 404**, because a client-facing URL that says "this one
expired" tells somebody probing which tokens are real.

`test_proposal_share.py` asserts all of it, including that a client with no
Hub login can open the page, that the Hub's chrome is not injected into it,
and that a rep can read the PDF without marking it read.

### The rate card is ours, and Expected Results is a framework

Two things a client should never have read, on the document they decide from.

**The rate card was named four times on a proposal a client receives** — the
PDF's rate note, the seeded ROI copy, the preview's default and the growth
note — while `DIRECTIVES` had been telling the model not to mention it since
the day it was written. That is the shape this codebase keeps finding: a rule
policed in the prompt and broken by our own strings, where no generated copy
was involved at all. Naming it invites the one question the document cannot
answer — *can I see it?* — and turns a quoted price into a list price somebody
might have marked up. The strings are gone, the directive stays, and
`proposal_spec.client_safe()` runs inside `clean_ai_text` so the rule cannot
hold only until somebody pastes. It drops the **sentence**, not the phrase:
swapping in "our rates" leaves copy that is grammatical about half the time
("Rates follow our rates", "adding one starts at our rates minimum"), and a
client reads the mangling rather than the intent — the Smart 1 Labs precedent,
which discards rather than paraphrases into something nobody wrote.

**"Expected Results & ROI" was a table of impressions**, which answers a
different question from the one its own title asks. An impression count is a
delivery figure: it says what the money bought, not what the business gets.
The insertion order has carried a **KPI Framework** all along — a primary KPI,
the secondary ones, what is reported monthly, and what each product is
measured on with a normal result for it — so the two documents described one
campaign two ways, and the client agreed to impressions while the campaign was
run against KPIs.

`hub/kpi_framework.py` is that one description now, and the proposal's ROI
section renders it. Three rules in it. A benchmark is a **range labelled as an
expectation**, said once in the client's own words ("what this inventory
normally delivers … not guarantees"), because a single figure printed under a
heading like that reads as a promise. A product the table does not know falls
through to *track against the campaign objective* rather than to the
nearest-looking row — a display benchmark against an audio buy is a number
nobody can hit. And **"not measured" is an answer**: a campaign with no KPI
chosen says which step chooses one instead of printing a confident framework
built from an empty list.

The IO builder still draws its screen from its own JavaScript copy of that
table, so `test_proposal_targeting.py` parses `benchmarkFor` out of the
template and requires the two to agree in **the same order** — the order is
load-bearing, since "video" tested before OTT reads every Connected TV line as
YouTube. `expected_results()` is kept and no longer rendered: it is the one
place that knows the delivery arithmetic — the quoted rate rather than the
listed one, a one-time line spread across the flight — and its docstring says
why nothing draws it.

**A list of things a client is meant to weigh is a list.** KPIs, success
metrics and audience layers were each rendered as `", ".join(...)` into a
sentence, so six KPIs arrived as a comma string and the fourth — the one they
would have argued with — was skimmed past. `proposal_spec.bullets()` is the
half of the bullet rule that covers the lists the *code* prints, the way
`_one_bullet_per_line` covers the ones a model writes; it returns a string, so
it goes through `blocks()` and each renderer draws the list it already knows
how to draw. What is printed beneath the KPIs excludes the KPIs themselves —
the metrics list repeated all four of them a line later, and a reader who sees
the same four twice stops reading the second list.

### A bullet inside a sentence is not a list

`clean_ai_text` normalised the markup and left the *shape* alone, and the
directive it works to actually asked for the wrong thing: "write the items as
sentences or separate them with the bullet character •". So a model obliged
with `We will reach three areas: • Carmel • Fishers • Noblesville` — one
paragraph, which all three renderers duly set as one paragraph, and a client
read a sentence with dots in it on the page listing where their money goes.
Nothing errored; the copy was even correct.

The shape is enforced now rather than requested, the Smart 1 Labs rule one
step on: `_one_bullet_per_line()` puts every bullet at the start of its own
line and keeps the lead-in above it, and the directive asks for one item per
line so the model mostly gets there on its own. What the renderers read is
`proposal_spec.blocks()` — paragraphs and lists, decided **once** — so the
preview builds a `<ul>`, the PDF gives each item its own bullet-indented
Paragraph and Word writes a List Bullet paragraph, and a fourth renderer
added later cannot go back to printing the bullet inside a sentence. The
browser carries the same split in `cleanCopy()`, because a rep pastes into
the section editor and must see it normalise there rather than in the PDF.

One thing it deliberately does not do: a sentence written *after* the last
bullet stays attached to that item. Where a list ends is not knowable from
the text, and cutting at the first full stop would split "Carmel, IN. 10
miles" into two items.

### The map is the part of the proposal the client can check

Geography was a table of sentences — "Carmel, IN + 10-mile radius" three
times — and it is the one section a client cannot read against what they
know, because the person reading it lives there. A map answers in a glance
what three sentences do not: that the rings overlap, that the whole buy sits
on one side of the city, that the suburb they care about is inside it.

`hub/target_map.py` draws it: OpenStreetMap tiles composed with Pillow, rings
computed from each radius, numbered pins and a key, and the tile attribution
printed **onto the image** — three renderers show this picture and a credit
written into one of them travels with none of the others. One PNG serves the
preview, the PDF and the Word export, so the three cannot disagree about
where the campaign runs. There is **no JavaScript map**: target areas and the
creative classifier each carry a mirror already and each needs a test proving
the halves agree, and a fourth renderer of the same fact is the cost this
codebase has already paid twice.

Every rule in it is a way to be confidently wrong:

- **A named state must match.** A geocoder handed "Carmel" answers
  Carmel-by-the-Sea, California — so an origin naming a state and finding
  nothing in it comes back *not found*, never the same name somewhere else.
  A map of the wrong Carmel is a wrong answer that looks exactly like a right
  one, and it is on a document a client recognises.
- **A DMA, a state and a national buy are not drawn.** There is no boundary
  data here and inventing a blob is a claim about coverage nobody can check.
  Those are *named under the map* as covered and not drawn, so the picture is
  never mistaken for the whole buy.
- **Four kinds of missing are four answers.** Covered-but-not-drawable, a
  spelling nothing could find, an area with no origin at all, and a tile
  server that did not answer. Only two are somebody's to fix, so only those
  two are offered as something to fix — and they are shown on the areas
  screen, where the fix is, never on the client's document.
- **A failure costs the picture and nothing else.** No blank grey box, no
  "map unavailable" graphic: the client document simply omits it, and
  `render()` returns `(None, reason)` so the builder can say why. A map made
  mostly of missing tiles is refused for the same reason.
- **The URL carries a signature of the areas.** A stale map is the worst
  failure available here — plausible, dated, and about somewhere else — so
  changing a radius changes the URL rather than letting the browser serve
  yesterday's picture.
- **The picture is bounded on both axes, and the crop stays landscape.**
  Scaling to the text column's width alone is only a bound if the picture is
  wider than it is tall — and three rooftops running north-south crop to a
  *portrait* map, which at that width is 8.8 inches high: most of page two,
  a half-empty page one above it, and one slightly taller campaign away from
  a flowable reportlab cannot place at all, which fails the whole PDF rather
  than the picture. `MIN_ASPECT` widens the crop (never trims its height —
  that would cut a ring off the thing the map exists to show), counting the
  key that will be drawn underneath it, and `MAP_MAX_H` caps what the page
  will draw whatever arrives. The map and its caption are one `KeepTogether`,
  because a caption orphaned onto the next page is a sentence about nothing.
  Found by building a real proposal and looking at it, which no assertion
  about a PNG's bytes would have done.
- **Taking it off is said out loud, not left to an icon.** A picture provokes
  exactly one question — *that doesn't look right, how do I get rid of it?* —
  and the answer was a 🗺 drawn at 45% opacity in a row of five section
  icons, which is the note `hub/templates/diagnostics.html` and the Smart 1
  Ads estimate's per-section pencils already make about a quiet control. It
  is a line of words under the picture, the removed state offers its own way
  back, and the areas screen says where the removal happens so the two
  screens do not each answer half. `showMap` is still one flag on the areas
  section, read by the preview, the PDF and the Word export.

`MAP_TILE_URL` and `MAP_TILE_ATTRIBUTION` are settings, so a deployment with
its own tile server (or a keyed one — the key rides in the URL) needs no
second code path. No provider key is asked for: this deployment has never had
a maps key, and a page inviting a credential nobody has set reads as broken
while the feature works perfectly well without one.

The route's first version caught **every** exception around reading the
quote and answered "quote could not be read", which turned an
`AttributeError` on a wrong column name into a 404 that looks exactly like a
proposal with no target areas — the whole feature silently absent, with
nothing anywhere saying why. Only a malformed blob is caught now.

### Eleven rooftops is not eleven trips through the area editor

The list already exists — in the email, the spreadsheet, the client's own
store locator — and typing it back in one box at a time is where a
multi-location campaign loses its third location. `target_areas.parse_paste()`
reads a pasted block, and `parse_places()` does the same for the competitors
and venues inside it. Deliberately two readers: a competitor line is a
business and an address, an area line is a geography and a radius, and one
parser trying to be both reads "Riverside Dental, 1200 Main St" as a city
called Riverside Dental.

Three rules, each about the way a paste goes wrong quietly:

- **Nothing is added by the reading.** The rows come back with a sentence per
  line saying how each was read and a rep presses Add. A paste that silently
  assumed ten miles on eight of twelve lines is eight decisions nobody made.
- **A line nobody could read comes back by name.** Twelve lines producing
  nine areas is a campaign missing three locations nobody can see are
  missing. The rule `knack_websites.py` applies to a value Knack would refuse.
- **A place is short.** A comma is not evidence — prose has commas — which is
  how "not a place at all, just a sentence somebody typed into the wrong box"
  became a target area with a ten-mile radius drawn on it. Over six words
  with no ZIP Code in them reads as a note.

A location already on the campaign is reported as a duplicate rather than
added twice, because pasting the whole list after adding one by hand is the
ordinary case.

### Who to go after is researched, and stays a suggestion until somebody ticks it

The client is the only person who knows who they lose business to, and they
are not in the room when the proposal is built — so the competitor list was
whatever the rep could remember. `/api/find-targets` researches it over the
web, scoped to the target areas already on the campaign, and refuses to run
at all with no area on it: a search with nowhere to look comes back with
national brands.

Everything it returns arrives `accepted: False`. Printing a researched list
on a proposal is us telling a client who their competitors are on a model's
say-so, and that is the paragraph a client checks hardest — the same rule
`modules/ads_builder` applies to its own competitor research. An address is
carried only where the model gave one, is labelled **unverified** on the
screen, and is never derived from the name: a geo-fence built on a wrong
address spends the budget outside somebody else's front door, the rule
`modules/ads_builder/logo.py` works to. A row with no address is a real
answer rather than a gap — conquesting by brand and behaviour needs no
location — and the screen says so in those words.

And the two empty answers are kept apart, for the reason
`connected_accounts_result()` gives in Google Finder: **"we could not look"**
is a 502 that says the campaign is unchanged, **"there is nobody worth
naming"** is a 200 that says so in as many words. Only the second one means
stop looking.

### Who built it is not who is on it

The proposals list showed `salesperson`, which is the sales contact *typed
onto the proposal for the client's benefit* — blank on most drafts and
sometimes somebody else's name entirely. So "who wrote this?" had no answer on
the one screen the question is asked. `created_by` is read off the Hub session
at creation and never rewritten, so the column cannot quietly become "last
touched by" while the heading says Created by; an uploaded proposal answers
the same question with its own field, and a row from before it was recorded
says *not recorded* rather than showing a blank somebody reads as nobody.

### The pipeline was knowledge the Hub had and told nobody

`hub/sales_status.py`, the **Proposals** card on the dashboard, and
`/api/sales/scoreboard`. Three phases of work gave the Hub real knowledge
about every proposal — who opened it and how many times, whether the pricing
still stands, whether the client accepted, what the campaign costs — and all
of it was readable only inside the Proposal Builder. The dashboard everybody
opens carried eleven KPIs about *live* business (clients, live products, live
budget, websites, billing) and **not one figure about pipeline**: nothing
quoted, nothing waiting on a client, nothing won and not yet trafficked. There
was no scheduled sweep either.

That is the shape `hub/social_status.py` already answers next door, and its
note applies word for word: there is no mailer in this Hub, so the honest
route is putting it where people already look.

**Five signals, kept apart.** *They have not opened it*, *they read it and
said nothing*, *the price lapses this week*, *the price has lapsed* and *they
said yes and nobody wrote the order* send somebody to five different actions,
and one "needs attention" figure covering all five is a figure nobody can act
on. A quote lands in exactly one of them.

**It reads the open book and nothing else** — Draft, Sent and Approved. A
Converted or Lost quote is finished, and walking every quote ever written on a
page that loads on every visit is the cost that gets a number turned off.

**A count is never a link to a page that cannot show it.** Each figure carries
`?focus=<signal>`, and the builder narrows its list to exactly the ids that
reading counted — not to a status tab that is nearly the same thing — saying
what it is showing and how to leave it. An empty bucket leaves the whole list
rather than an empty table that reads as a book with nothing in it.

**Each zero says which kind of zero it is.** "Nothing is waiting on a client"
and "no client link has ever been sent for any of these" render identically as
a nought and only the second is somebody's to fix.

**One reading, two screens.** The Proposal Builder's own dashboard is handed
the same block by `/api/dashboard`, because two screens answering "what needs
chasing" separately is how they come to disagree in front of the same rep —
the `/api/db/structure` versus `/api/integrity` trap. And the route is
deliberately **not** a Utilities path: the presence headcount already showed
what happens when a figure everybody sees is served by a path most accounts
are refused.

**Nothing is written anywhere.** It does not touch a quote, it does not record
having looked, and it deliberately sends nothing to Smart 1 Suite — a nudge
onto a client's CRM record is a different decision from surfacing a number on
our own dashboard. `test_sales_status.py` asserts that from the **AST** rather
than the text, because the module's own docstring names `ghl_hooks.py` as the
precedent it follows and a check that reads prose as a call site reports the
explanation as the defect.

### One proposal, three monthly figures, and a fourth on the insertion order

`campaign_cost()`. `summarize_into()` took `monthly_budget` from the selected
package or from `state["budget"]` — the number typed on the Budget step, which
is what the client **asked for** — while the media plan totalled the lines
actually being bought and `ioDataPayload()` billed those same lines. Editing a
line is the ordinary case, and the moment one is edited the document says four
different things:

| On the document | Monthly | Campaign |
|---|---|---|
| Cover | $8,000 | $48,000 |
| Media Mix & Budget Allocation | $5,750 | $34,500 |
| Investment Summary | $8,000 of media | $51,594 |
| The insertion order | $5,750 | — |

$2,250 a month between the document a client signs and the order that bills
them. Nothing errored, and every screen was internally consistent, which is
why it survived.

**The plan is the number.** Once there are line items they are what is being
bought, and the cover, the media plan, the investment summary, the packages,
the IO and the dashboard's pipeline all derive from `campaign_cost()`.

Four rules in it. **Recurring and one-time are never added together** — a
$1,500 shoot is not $1,500 a month, and the old `sum(i["dollars"])` fallback
said it was. **A line runs for its own term**, read once rather than in each
caller. **The Suite licence is not campaign cost**: it is a separate product
with its own line, and blending it is how a client comes to believe the
platform stops costing money when they pause the media. And **a quote with no
plan yet still answers**, with the ask, so nothing has to branch on whether
there are items.

**The working budget follows the plan, and what the client asked for is
kept.** `syncBudgetToPlan()` runs from both editors and from a change of
basis, because otherwise the number depends on which screen the rep happened
to edit the plan from; `budgetAsked` records the conversation and is never
overwritten; and the Budget step **says when the two have parted company**,
because a rep who set $8,000, built a $5,750 plan and came back reads $5,750
with no explanation and assumes the tool lost their answer. The **Recommended
package is the plan exactly** rather than the plan rounded to the nearest
$250 — a package table saying $5,750 beside a media plan saying $5,730 is the
disagreement this whole change exists to end.

**Every figure is labelled with its scope, and same-scope figures agree.**
There is no single number for two different questions: the cover and the media
plan's totals row are the campaign's own cost, and the Investment Summary adds
the licence and says *including licensing* on its total. What is forbidden is
two figures with the same label disagreeing.

**Two more figures the same mistake was hiding.** `creative_needs.medium_spend()`
multiplied *every* line by the flight, so a one-time production read as six
times its cost — printed on the client's creative section, and, worse, it
switches the comp confirmation **off**, since that question is only asked where
the spend is *below* the threshold, which is exactly the small campaign it
exists for. And the **Recommended Channel Strategy** table listed every line,
so "Video Production — top of funnel, builds awareness and trust on the screens
the household already watches" and "Management Fee — supports the campaign"
were printed on a document a client reads; `channel_lines()` keeps the lines
that are channels, and the preview filters through the same reading rather than
keeping its own.

**And the insertion order was handed the buy-side rate.** `lineForIO()` sent
the card's own rate, so an IO read `CPM 4.25` for a line the client had been
quoted at $8.50; it sends `sellRateOf()` now. Its management fee field asks
for "an amount, percentage, INCLUDED, or NONE" and was being handed
*"Yes — per rate card"* — not an amount, and our pricing sheet named on a
document that reaches a client. It is read off the plan's own fee lines, or
**NONE**, which is a real answer and the word that field expects.

`test_campaign_cost.py` asserts all of it, including that the browser keeps no
copy of the arithmetic.

### The insertion order is a record, not a PDF and a webhook

`hub/io_records.py`, the **Orders we have sent** card on Client 360, and
`/api/client/orders`. Submitting an insertion order allocated a number from a
Postgres sequence, built two PDFs into Cloudinary, wrote one line in the
activity log and POSTed the whole campaign to Smart 1 Suite. **Then it kept
nothing** — no orders table, no list of what had been sent, no way to reopen
one, and no answer to "what have we written for this client" until the campaign
appeared in Knack weeks later. A rep asked what went out in July opened
Cloudinary, or asked whoever built it.

Three things followed from that and all three were live. `hub/io_reconcile.py`
had to be assembled out of the **activity log**, which rotates — so the one
report about orders that were never trafficked could see only as far back as
the log did. That log line carried an **empty order number** on every entry the
route had ever written and nothing noticed for months, because nothing read it.
And the log line, the client overlay and everything else were written at the
**top** of the route, *before the request was validated* — so a submit refused
for missing documents still logged an order and still registered the client,
and the reconciliation would have reported it as a campaign nobody set up. All
three are one `_keep()` now, reached at every exit past the point where the
documents exist, so the three records cannot disagree about what was submitted.

Five rules on the store.

**One file per order, never one file holding all of them** — the
`hub/drafts.py` rule, and the stakes here are a signed document rather than a
draft. **A resubmission updates the order rather than adding a second one**: a
correction sent an hour later is the same order at a new revision, and two rows
under one number is how a client record grows three identical entries with no
way to tell which is current, which `upsert_from_ghl` learned from GoHighLevel
first. Each attempt is appended to a short history, and the **first**
submission's date survives — an order written in July must not be re-dated to
the day somebody fixed a typo in September.

**The row is written whether or not Suite took it.** An order the client has
been sent is an order, and "delivered" and "built, and Suite refused it" are
different states that send somebody to different places. That is three facts
rather than one: `delivered` is what the latest attempt did, `ever_delivered`
is whether Suite holds *any* version — a correction that failed after a first
submission that landed leaves Suite holding the old one, which is real and is
not the same as an order that never arrived — and only the second means the
order reached neither system.

**What is stored is the agreement, not the wizard.** The campaign state is tens
of kilobytes of answers, working notes and generated copy; a record has to
answer who, what, when, how much and where the document is. Lines are capped,
the row is capped, and a row too large drops its **lines** rather than being
refused — who and how much are what it exists for, and a record refused for
size is an order with no trace at all.

**And a number handed out that never became an order is deliberately not
tracked.** The sequence issues one at the *start* of the wizard, so an
abandoned IO burns a number and leaves a gap in the numbering — and nobody
here asks about those. A note recording them was built and then removed:
machinery kept alive for a question nobody puts is machinery to maintain, and
this file already counts five integration points that were declared and never
wired. This store records orders that were sent.

**The reconciliation reads the durable half now**, so its note stops saying the
activity log is the horizon — that sentence was true and would have gone on
being printed while understating what the report can see. It also names the
orders Suite never took and the numbers that never became orders.

**On the client record it is deliberately its own card.** Products & IOs is
Knack's answer to *what is running*; this is the Hub's answer to *what we
sent*, and the two exist at different moments — which is the whole point for a
client written up on their first IO, whose record is otherwise empty until
somebody sets the campaign up. Whether Knack has the campaign is read off the
products already on the page rather than from a second reconciliation that
would come to disagree with the first, and the flag is **not drawn at all**
when that card did not answer: absent data must not read as a finding. The
route is under `/api/client/` because `hub/suite_embed.EMBEDDABLE` allowlists
that prefix — a card pointed anywhere else renders on every screen except
inside the Suite frame, and fails silently there. `test_io_records.py` asserts
all of it.

### What is mapped in Knack, and what is still somebody's assumption

`hub/knack_map.py` and **QA → Data Quality → Knack Field Map**. Knack is the
system of record and this Hub reaches into it from nine modules, and there was
no one description of what it thinks each object and field is — so "which
mappings have we confirmed?" could only be answered by reading nine files and
holding the answer in your head. That question matters far more the moment
more is pushed into Knack: a field id pinned to the wrong column writes into
the wrong place on a live record, and Knack refuses the **whole** record over
one bad value, so an unconfirmed mapping costs the write rather than the field.

**It is not a second copy of the field ids.** Nine modules pin them and each
owns its own; a copy here is the drift `hub/config.py`'s ALIASES table and the
two rate cards have each paid for. `fields()` imports from the owning module —
`knack_api.field_ids()`, `knack_api.SUPPORT_FIELDS`, `knack_websites.FIELDS`,
`ad_copy.field_ids()`, the `knack_products` constants — so a field repinned
there moves here with no edit, and one that stops existing cannot linger in a
table nobody re-read. `test_knack_map.py` asserts that by repinning a constant
and requiring the map to follow, and asserts the file carries no `field_<n>`
literal of its own.

**What lives here is the part no module holds**: which object each map belongs
to, which tool creates the records, whether the ids are pinned or matched by
label, and whether a person has confirmed the mapping against the live builder.
Today that is **110 fields across 7 objects, 64 of them written**.

**A field is confirmed once, against the object, and every tool inherits it.**
Object 135's monthly cost is read by Client 360, the scorecards, the billing
reports and the IO reconciliation; checking it four times is four chances to
disagree. So a confirmation is keyed on object and field rather than on tool.

**A confirmation is a person, a date and a field** — never an object. "We
checked object_153" is the kind of assurance nobody can act on later, and the
point is to be able to say which of the eighteen were looked at. **The id is
stored with it**, so repinning a field *retires* the tick and the row says
**superseded** rather than carrying a confirmation from one column silently
onto another, which is the single way this record could become worse than
having none.

**A field matched by label is a finding, not a mapping.** `object_140`
(Campaign Change Requests) is still matched by label and is a **write target**;
`hub/knack_api.py`'s own comment says why that is dangerous — a renamed label
breaks label matching silently, which is exactly the state `object_107` was in
before its ids were pinned. It is reported as unpinned rather than listed as
though it were confirmed.

**Without Knack the live check is not measured, and the report is still
measured.** `verify()` reads the schema and says what Knack calls each id; with
no credentials it refuses rather than drawing ticks nobody earned. But the
report itself still answers, because the map *is* what it exists to show and
calling the whole thing unmeasurable would hide the record somebody is meant to
work down. The two halves are said apart.

**Nothing here writes to Knack.** It reads the schema and writes one small
Hub-side overlay of confirmations; the test asserts that from the AST, and that
the module does not import `requests` so it could not reach an API by accident.

**The five write paths in daily use are not gated on this.** Tickets, campaign
support, ad copy, the dashboard-URL button and the website record are live, and
switching them off until a hundred and ten rows are ticked would break working
tools to make a record tidy. What the map does is say which of them are running
on a mapping nobody has confirmed — 64 of 64 today — so that is a list to work
down rather than a gate that fires on the wrong day.

### An order we sent, and the campaign nobody set up

`hub/io_reconcile.py` and **QA → Data Quality → Orders With No Campaign**.
Submitting an insertion order does three things: it writes an activity-log
entry, it registers the client as an overlay when nobody has heard of them
(`hub/io_clients.py`), and it POSTs the order to Smart 1 Suite. Then the IO
Builder's job is over, and **nothing ever checked that the campaign was set
up**. An order signed in March whose products were never written into Knack
looks exactly like one that was: the log says it went, the overlay row goes on
standing in for a record that never arrived, and Client 360 keeps saying the
cards are empty because there is nothing to read — which is the sentence
`io_clients.py` added for a client who is *new*, not for one whose campaign
was dropped. Nobody is billed, nothing is trafficked, and the first person to
find out is whoever eventually asks why a client we wrote an order for has no
products. Both halves were already here — what we sent is in the activity log
and, for a converted proposal, on the quote; what landed is on Knack's
products, each carrying its IO number — and nothing compared them.

**Underneath it was a defect that would have made the report useless on the
day it shipped.** `submit_io()` logged `order=_body.get("order_number")`
against a payload whose key is `orderNumber`, so **every `io_submitted` entry
that route has ever written carries an empty order number** — while the
`client_registered` entry written three lines below it, through
`io_clients.register_from_io`, read the real key and got it right. Two readers
of one payload, the wrong one is the record a reconciliation depends on, and
nothing errored at either end. The entry now also carries the media partner,
the flight start and the monthly, because a chase list needs to know who to
ask and whether the campaign should already be running, and neither is
knowable from an order number.

**A source that could not be read is not measured, and this is the strongest
case of that rule in the Hub.** `knack_products.rows()` never raises: it falls
back to a stale cache, then to the private fallback, then to nothing. Read
against the fallback — a snapshot refreshed out of band, whose rows are the raw Knack
records rather than `_row()` output and so carry no IO number at all — *every*
order reads as never trafficked, which is a report accusing the whole traffic
team on the strength of a stale file. So the products must have come from
Knack itself, or this answers `measured: False` and says why, and
`report_cache` never freezes that into the shape of "there is nothing to see".

**An order newer than the product read is not judged at all.** A stale cache
is a real Knack read of an earlier day, and an order written after it was
taken could not appear in it however long ago it was sent — so those are
counted as waiting with the reason named, rather than the whole report being
refused over a cache that is perfectly good for everything older than itself.
**An order submitted this morning is not late** either: setting a campaign up
is not same-day work, so `GRACE_DAYS` is a week, and a report that fires on
every order the day it is written is one nobody reads.

**"Late to be set up" and "should be live right now" are two different
conversations.** An order whose flight has already started is running in
nobody's system — not trafficked, not billed, and the client is expecting it —
so those sort to the top, are counted apart and are drawn red, while a merely
late one is amber. A page of red is a page people scroll past.

**An order with no number is its own finding, not a missing campaign**: there
is nothing to look up for it, which is a different thing to do about it. And
**the activity log rotates**, so the note says how far back it can see rather
than implying it looked at everything — a converted proposal is the half that
does not rotate, because those live in the quotes table.

**A row somebody has settled leaves the list, and the mark is applied on
read.** Some orders are never going to appear: cancelled before trafficking,
renumbered, a test. Left in, they are permanent red on a report whose whole
job is to say what to act on this week — the failure `hub/creative_evergreen.py`
was written for — and the mark is read on every run rather than baked into the
cached rows, because there are two gunicorn workers and one folded into a
cached payload is a button that appears to do nothing to whichever worker did
not take it. The reason is one of a short list rather than free text, the
control reads that list off the payload so a screen cannot offer what the
write refuses, and the mark records **who and when**: a decision about a
campaign that nobody can attribute is one nobody can revisit.

**Nothing here writes to Knack, to Smart 1 Suite or to a quote.** The settle
mark is a small Hub overlay through `jsonstore` and everything else is a
reading. `test_io_reconcile.py` asserts all of it, from the AST rather than
the text — this module's own docstring names Suite and `io_clients.py` as the
things it does not touch, and a check reading prose as a call site reports the
explanation as the defect.

### And is it the campaign we sold?

`hub/io_reconcile.delivery()` and **QA → Data Quality → Campaigns Not At Order
Value**. *Orders With No Campaign* asks whether a campaign exists; this asks
whether it is the one that was sold. It is the next link, and the one
`hub/io_records.py` made possible — before the order record there was nothing
on our side to compare against, because the only trace of an order was a log
line carrying a number and a client name.

**The finding is the money, and the counts are never the finding.** An
insertion order of six lines may be trafficked in Knack as six product rows or
as one, and nothing readable from here says which convention this book
follows. A check that fired on every order because the shop writes one row per
campaign is a check somebody switches off within a week — the note
`hub/qr_codes.py` makes about a warning that fires on every social spot. So
the line counts are printed beside each row as context and no row is ever
raised for them; what is compared is the **monthly**, which is the same number
however many rows it was split across.

**Both figures are always shown, and so is the difference.** A report that
says "discrepancy" without printing the two numbers behind it is one nobody
can check, and the first person who finds it wrong stops reading the rest.

**Over and under are different conversations.** A campaign trafficked for less
than the order is delivery a client paid for and is not getting, and is drawn
red; one trafficked for more is billing nobody wrote an order for, and is
amber. They are counted apart and the row says which.

**A tolerance, and it is ours.** Nobody publishes one, so
`MONEY_TOLERANCE_PCT` / `MONEY_TOLERANCE_MIN` carry `TOLERANCE_SOURCE =
"house"` and the page says so in words — the rule `HOUSE_LEGIBILITY` in
`services/abcd_service.py` already works to. A campaign trafficked to the
exact dollar is not the normal case: a rounded rate and a part first month are
ordinary, and calling every one of those a finding is how a list stops being
read.

**A product row with no monthly cost is never counted as zero.** A blank there
would drag the campaign's total down and read as under-delivery invented out
of a field nobody filled in, so an order with any such row is *not measured*
and is listed with the reason — and its "In Knack" cell is a dash rather than
the partial total, because a figure printed beside that sentence is one
somebody reads as the answer.

**An order with no campaign at all is left to the other report.** Raising the
same order on two screens is how a reader learns the two disagree. It
inherits the rest: the products must have come from a live Knack read, an
order newer than that read or inside `GRACE_DAYS` is not judged (a campaign
part-entered is not a campaign short-delivered), and a settled order is out of
both. `test_io_reconcile.py` asserts all of it.

### A price with no end on it

`hub/quote_validity.py`. `VALID_STATUSES` has carried **Expired** since the day
it was written — a badge color, a ⏰ in the status picker, and nothing anywhere
that set it, so it was reachable only by a rep remembering to click it, which
in practice meant never.

That was cosmetic until the client got a link. `/sales/builder/p/<token>` lets
a client accept a proposal themselves, and the accept route checked that the
link was live, that the reader was not staff, and that this revision had not
already been accepted — and **nothing at all about when the quote was
written**. So a March link could be accepted in September at March's rates,
filed as a clean acceptance with the client's name and a timestamp on it,
while the rate card and the sell multiplier had both moved underneath it.

Six rules, each a way to be confidently wrong:

- **Only a document the client was given can expire.** A Draft was never
  sent — an old one is *abandoned*, a different word and a different thing to
  do about it; an Approved quote is one they said yes to, and expiring an
  acceptance takes back an agreement; Converted has an insertion order behind
  it. `Sent` and nothing else.
- **Derived on read, never stored** — the `hub/creative_evergreen.py` rule.
  Two gunicorn workers, so a status written by whichever one ran a sweep is
  one the other disagrees with, and a stored `Expired` would survive an
  extension: a quote reading as dead on the one screen a rep would go to
  revive it. `status` stays exactly as stored and `shown_status` is the same
  fact with the clock applied, so nothing can round-trip a derived value into
  the column.
- **The clock starts when the client could first see it**, which is the send
  rather than the writing — and *which date answered* is carried and printed,
  because "thirty days from when I sent it" and "from when I wrote it" are
  different promises and the client is holding one of them. Re-sending
  restarts it, since a re-send is the current document at current rates.
- **A quote with no date at all is not measured**, never expired. An absent
  timestamp reading as "expired today" would refuse an acceptance a client is
  entitled to give.
- **The client is never turned away.** Past the date the page says so *above*
  the document — the embed is 78vh tall, and underneath it a client reads four
  pages and only then finds out the price is stale — and names who to ask, with
  the accept form replaced rather than the page 404ing. A revoked or invented
  token still answers 404, because saying "that one expired" tells somebody
  probing which tokens are real; an expired quote is a real quote belonging to
  a real client who is trying to say yes. The accept route refuses in the same
  words, so the rule is not one the form merely keeps.
- **A window a rep chose is refused when it is out of range, not clamped.**
  Somebody who typed 3650 and got 365 has been told something different from
  what they asked, on a date a client relies on. It lives in the quote's own
  data blob — `create_all()` adds no column to an existing table — and is set
  from the share panel, where the send happens.

The follow-up nudge on the dashboard says which kind of follow-up it is:
"chase this" and "this one needs re-quoting before they can say yes" are
different jobs, and the second has a client sitting in front of a page that
will not let them accept.

### Two systems disagreeing about whether a proposal was won

`hub/ghl_hooks.sync_quote_status()`. The push into Suite has always recorded
`suite_opportunity_id` on the quote and **nothing ever read it back**: a deal
marked Won in Suite updated the client's Proposals card and left the Proposal
Builder's dashboard — the screen a rep actually looks at — still saying Sent.
Neither screen said the other existed.

Four rules. It matches on the **opportunity id and nothing else** — never the
client name, which for a client with three quotes is the guess
`hub/client_key.py` exists to refuse. **Only the decided outcomes write**
(`won` → Approved, `lost` → Lost): "open", "quoted" and "viewed" tell us
nothing the Hub does not already know better, and letting them write would
walk an approved quote backwards to Sent because somebody dragged a card in a
pipeline. **Converted is never moved** — an insertion order exists, Suite has
no way to know that, and it is the one change nobody could undo from either
screen. And **a status that changed by itself says who changed it**, on the
quote's own activity strip as well as in the Hub log, or a rep reading "Lost"
has no way to find out why. It reuses the module `wsgi.py` loaded (`salesb_app`)
rather than importing a second declarative mapping of the same tables, and it
can never fail the webhook: the client card is written either way, and
GoHighLevel retries a non-2xx.

### Delivery figures belong under the media plan

`media_plan_rows()`. Impression counts came off the client document with
"Expected Results & ROI", correctly — an impression count answers what the
money *bought*, not what the business gets, and printed under that heading it
read as a promise about outcomes. But it left the proposal with no answer to
the question the media plan itself asks, and a client comparing two proposals
had no way to tell a $4.25 CPM apart from an $8.50 one.

It is `expected_results()`'s arithmetic and never a second copy: the **quoted**
rate rather than the listed one, a one-time line spread across the flight and
labelled *once* rather than multiplied by it under a per-month heading, and a
management fee reporting **no units at all** rather than a plausible number.
The words travel with the figures — an estimate printed bare reads as a
guarantee — and the lines that are *not* in the headline total are named
rather than quietly under-reporting the campaign.

**One reading, three renderers.** The PDF drew five columns and the Word export
drew four, so one client's proposal already said two different things depending
on which file was sent; both read `media_plan_rows()` now, and the builder's
preview is handed the server's rows on the quote payload rather than carrying a
**fourth** copy of the arithmetic — the mirror this codebase has paid for twice.
A row priced against a budget that has since been edited reads *recalculating*
rather than stating a confident wrong number.

**And the copy above it was contradicting it.** The seeded media-plan paragraph
said "every rate is the Smart 1 card rate — there is no markup between the line
item and what runs", printed directly above a table quoting CPM at 2x, on a
document a client reads. `client_safe()` was written against "the rate card"
and this said "card rate", so the rule passed a sentence written for exactly
it; it matches both orders now, and the seeded copy says what the split is and
nothing about markup.

### An interruption cost the work in one builder and the place in the other

`hub/drafts.py`. Fifteen minutes of concentration is what an insertion order
or a proposal takes, and a rep almost never gets fifteen uninterrupted
minutes. Both builders already had half of the answer and each was missing
the other half, in opposite directions.

**The Proposal Builder saved the work and lost the place.** It autosaves to
the server on every keystroke, so nothing was ever lost — and `editLoaded()`
set `step=0`, so reopening a quote put the rep on step 1 of 14 pressing
Continue until they found the media plan they had been on. The position is
`S._step` now, stamped on every save and read back on open. **Inside the
quote's own data blob, never a new column**: `create_all()` adds no column to
an existing table, so one here would be silently absent on the live Postgres
with every local test green — the `client_key` rule. It is **clamped** on
read, because that number was written by whatever version of the wizard saved
it and an index past the end throws inside `renderStep()`, which is a blank
builder over a quote whose answers are all perfectly intact. And arriving on
step 7 **says so** and offers step one: a wizard that opens somewhere other
than the beginning with no explanation reads as having skipped ahead, and Back
from there is six presses. The proposals list carries it too — *Left at step 7
of 14* against a Draft, because "which of these was nearly finished?" is the
question somebody scans that list asking.

**The IO Builder kept the place and could lose the work.** Its draft went to
`localStorage`, which is the right instinct and survives exactly one browser:
somebody interrupted on their laptop and picking the IO up on a different
machine found no draft at all, and — worse — nothing on any screen said an
unfinished IO existed, so it was simply started again from the top. The local
copy stays, because it is instant and it is nearly always the right one; what
is new is a copy on the **server** and a list of them on the start screen.

Six rules in the store, each a way a draft quietly becomes a liability:

- **One file per draft, never one file holding all of them.** Two reps
  autosaving at the same moment would each write the whole collection back and
  the second write would drop the first one's work, which is precisely the
  failure a draft store exists to prevent.
- **Through `jsonstore`, so it outlives the disk** — and deleted through
  `jsonstore.delete_json`, never `os.remove`, or the mirror restores it and
  the discard undoes itself.
- **Nothing in it may raise.** A draft is insurance and insurance that breaks
  the thing it insures is worse than none, so every entry point returns a
  value, every route answers 200 with what happened in the body, and an
  autosave that could not write costs the server copy and never the IO
  somebody is in the middle of.
- **Bounded, and never in silence.** A per-owner cap and a per-draft size cap,
  because an autosave loop that fills the 5 GB disk takes the whole Hub with
  it — and when the cap drops the oldest draft the save **names it**, since a
  draft that goes missing quietly is the thing the feature exists to stop.
- **A colleague's unfinished IO is on the list too**, marked whose. Hiding it
  is how the same insertion order gets built twice. What the listing does not
  carry is the state blob: it is read into a page, and the blob is fetched
  only when a draft is actually resumed.
- **"Nobody has one" and "we could not look" are different answers**, the
  `connected_accounts_result()` rule — the list says which rather than drawing
  a clean empty panel over a store it could not read.

Three things the browser half has to get right. The autosave and the discard
**carry the mount** (`{{ request.script_root }}`): a root-absolute
`/api/draft` leaves this module and reaches the hub app, the trap
`test_landing_embeds.py` exists for. A closing tab is exactly the interruption
this is for and a debounce has usually not fired yet, so `pagehide` posts
through `sendBeacon` — which returns a boolean nobody reads, so a wrong path
there fails in total silence. And both deletes are `keepalive`, because
submitting the IO and pressing Reset are each followed by a navigation that
cancels a plain fetch, which would leave a finished IO sitting on the
unfinished list.

Starting a new IO and throwing the old one away are **different statements**.
The boot prompt's Cancel drops this browser's copy alone; the server draft
stays on the start screen, where discarding it is a deliberate press with the
name in the confirmation. Reset clears both, because its confirmation says it
clears the saved draft and that sentence has to stay true.
`test_drafts.py` asserts all of it.

### Every medium is asked about before it is priced

`hub/creative_needs.py`. A Connected TV or digital radio buy that reaches an
insertion order with no spot behind it is a launch date nobody can hit, so
those mediums are gated: does the client have creative, and if not does
the client pay or does Smart 1 comp it. **A comp on a medium spending under
the point where production pays for itself gets one explicit confirmation,
with the number shown**, and that confirmation lapses if the budget is later
cut below what was confirmed.

**Display and retargeting are gated too, and the paragraph that used to
exclude them was half right.** Six banner sizes genuinely is a $250 rate-card
line and genuinely is produced routinely — and none of that answers the
question, which is whether anybody has *asked*. A display plan reached the
insertion order with the creative box empty exactly as often as a CTV one did;
it simply cost $250 and a week rather than a shoot, so it was discovered at
trafficking instead of at launch and nobody called it a failure. Retargeting
is asked **separately** from display because it is a different set of files in
practice: the same six sizes carrying the offer that brings somebody back,
not the one that introduced the brand. A plan with both that answers once has
answered for one of them.

What stops that becoming noise is that the confirmation threshold is **per
medium** — `COMP_CONFIRM_BY_MEDIUM` puts banners at $500 against video's
$1,500 — because a warning that fires on every plan is a warning nobody reads,
which is the note `hub/qr_codes.py` makes about QR on social.

**"Do you have banners" has no answer until somebody says which sizes.** A
client who hands over a 300x250 and nothing else has answered yes and blocked
the buy. `required_units()` reads `hub/creative_specs.py` — the same S1M
CREATIVE SPEC KIT the IO's upload manager checks every delivered file
against — rather than restating it, so the proposal cannot ask for a set the
IO then refuses. Two rules on how that reads. A unit is **described in the
terms it is specified in**: an audio spot has no pixel size, it has a length
and a bitrate, and listing sizes alone made the audio row read *300x250* —
the **optional companion banner** presented as the whole requirement, which
is how somebody sends a banner and no spot. And the **ask leads**: a display
buy is a set of sizes and the HTML5 package is another way to deliver the
same set, so naming it first read as an extra thing to produce. A product the
kit maps no unit for is *not measured*, never an empty list rendered as
"nothing needed".

The classifier is the whole gate, and it cannot work from the rate card's
categories: four programmatic **video** products are filed under DISPLAY
beside banner inventory, and three of the four have names that identify
nothing — "Programmatic - Targeted" is $17.00 CPM video while "Category" next
to it is $4.25 CPM display. So those four are named explicitly, and
`/api/integrity` has a high-severity check that fires if one is renamed on the
card. Without it a renamed product silently reverts to the keyword guess, gets
read as display, and the gate stops asking while every screen still looks
healthy.

The wizard carries a JavaScript mirror of the classifier and both constants so
the Creative step reacts as a rep edits the plan; `test_proposal_spec.py`
asserts the two agree on every product, exactly as `test_target_areas.py` does
for the area helpers.
