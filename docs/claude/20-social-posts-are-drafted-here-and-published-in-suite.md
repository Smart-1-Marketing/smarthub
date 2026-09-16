## Social posts are drafted here and published in Suite

`modules/social_planner` (`/tools/social`) builds a client's month of organic
posts in one pass. `hub/social_plan.py` is the spec — channels, post types and
their mix, the calendar arithmetic, the copy checks and the CSV layout — read by
the module, the exporter and the AI prompt alike, the same way
`hub/proposal_spec.py` is.

**It stops at a CSV on purpose.** Social Planner's write API needs
`social-media-posting.write`. That scope is now in `hub/ghl_scopes.py` and is
asked for at consent, but *requested is not granted* — until the agency
re-consents and the Suite panel's scope report shows it granted, the write path
does not exist. Ending at the bulk-upload CSV means the drafting pipeline earns
its keep while that is pending, and works regardless of whether it ever lands. When
it does, `PickerClient.ghl_location_id` already holds the sub-account id per
client — that mapping does not need building. **Resolve a client to a location
by domain, never by name**: posting to the wrong sub-account publishes one
client's content on another client's page, which is the worst outcome any tool
in this Hub can produce.

**The copy checks are code, not prompt text.** A price, a percentage, a phone
number or a deadline that is not in what the strategist typed is a *blocking*
flag and the plan cannot be approved with one outstanding — unlike a proposal,
which a rep reads before a client does, a month of posts is bulk work that gets
skimmed. Superlatives warn. Channel limits block. `test_social_plan.py` asserts
all of it, with deliberately plausible fixtures: the failure mode is not
gibberish, it is confident and wrong.

**Tone is a set of options, not a text box.** A free-text tone field got one of
three answers — nothing, "professional", or a sentence pasted since 2023 — so
every month came out in the same middle register. Each option in `TONES` carries
the *instruction* rather than the label ("write the way you would talk to
someone over a fence"), several can be combined, and the free-text box survives
beside them for a client whose voice is genuinely their own.

**The month is asked what it is promoting.** Without `promote`, the model
spreads itself evenly across everything the business does, which is how a plan
ends up promoting the service they least want more of. It is a focus, not a
mandate: the prompt says not to force it into an educational or community post,
and a service named there counts as authorised text, so mentioning it is not
flagged as invented.

**Social media holidays are offered as fill, and the list is ours.** The middle
of a month drifts into generic filler because a local business does not have
twenty things happening; a dated hook the audience recognises is better filler
than an invented one. There is no authority publishing "national days" and the
lists that circulate contradict each other, so `HOLIDAYS` is deliberately short
and checkable, every row says `source: house`, and the screen says so. The
moving ones — Thanksgiving, Mother's Day, Memorial Day — are **computed**, never
listed: a hard-coded date is right for one year and quietly wrong every year
after, and the calendar still renders. Days carry `tags`, so one tagged for
retail is not offered to a roofing company. A holiday landing on an existing
slot is attached to it; one landing on a non-posting day adds a slot, because a
day you wanted to mark is no use marked three days late.

**There is no JavaScript mirror of the grid.** Target areas and the creative
gate each carry one so a wizard can react live, and each needs a test asserting
the two halves still agree. That cost is paid twice already; here the calendar
is one API call and the browser renders what comes back. Keep it that way.

The page hands the browser its vocabulary in a `<script type="application/json">`
blob rather than interpolating Jinja into a real script block, which is why
`tools/jscheck.py` can hand that block to `node --check` instead of skipping it.
`tools/pagecheck.py` did not know that script types other than JavaScript exist
and failed the page for a syntax error in something that was never code; it now
shares jscheck's `NON_JS_TYPES` list.

### The layer above a month plan: requests, ideas and the push

`hub/social_content.py`, `modules/social_planner/intake.py`, `ideas.py`,
`agent.py`, `links.py` and `suite_client.py`. The planner drafts a month for a
client we manage; everything here is what surrounds it — where an ask came
from, who asked, what the client is being offered to swipe on, and what has to
be true before anything reaches Suite.

**It is in the planner and not in a module beside it.** A `modules/social_content`
next to `modules/social_planner` would be a second description of what a post
is, which is the failure the Proposal Builder cost a year: the same client
quoted two ways depending on which of two tools a rep opened. A month drafted
by a strategist, an idea a client liked and a photograph a location manager
sent in converge on the same slot, in the same batch, going out through the
same export or the same push. `hub/social_content.py` sits beside
`hub/social_plan.py` for the same reason the proposal spec sits beside the
rate card: it is data and arithmetic with no Flask in it, read by the module,
the client pages and the test alike.

**One client account is one social presence and often several shops.** A
location here is a Hub-only organizing idea — it sorts and attributes a
request and never gets its own posting destination, because one shared page is
what the client has. There is one signed link per **client**, not per
location: a link per location is the inbox-per-shop arrangement this replaces,
reissued as URLs. The link is *derived* (`itsdangerous` over `SECRET_KEY`), so
it is the same string every time, cannot be created twice and cannot go
missing; the only thing written is a small revocation list, and a revoked link
answers exactly what a link that never existed answers — the rule
`modules/ads_builder` settled for the client estimate.

**A client's own words are authorization, and this is the load-bearing half.**
`social_plan.validate_copy()` blocks a price, a percentage, a phone number or
a deadline that appears in copy and in none of the facts a human typed,
because a model inventing "$50 off through Friday" gets the client a phone
call about an offer they never ran. A location manager typing that sentence
into the request form **is** the supply. So a promoted request carries its own
words onto the slot as `supplied`, and `validate_slot()` merges them — in that
one function rather than at each call site, because a rule two of three
callers keep is not a rule. Without it the tool blocks the client's own offer,
on the client's own request, and reads to a strategist as broken rather than
as careful. Nothing errors either way.

**A flag is computed on read and never stored, and never crosses a client.**
Overdue and possible-duplicate are both functions of today and of the other
rows as they stand now; baked in at write time, a request that went overdue
overnight stays green until somebody edits it, and there are two gunicorn
workers to disagree about which copy is current — the reason
`hub/creative_evergreen.py` applies its mark on read. Duplicates group by
client first: two businesses wanting the same Friday is a Friday, and a queue
full of pairings nobody can act on is a colour people stop reading. It is a
plain date-overlap test on purpose — fuzzy matching on a location manager's
own wording produces a confident wrong pairing, and the whole point is that a
person reads both and decides. **Nothing is auto-merged and nothing is
auto-declined.**

**ASAP is a real answer and is not converted into today.** A request that says
"whenever" is not overdue the moment it is submitted, and treating it as
naming today would flag every one of them by tomorrow.

**A turnaround time is measured or it is not promised.** The confirmation
screen wants to say "ready to review by X". `turnaround_note()` computes it
from the requests actually triaged and says *not measured* until there are
three, rather than quoting a plausible number nobody has checked into a
commitment made on the client's behalf. `mark_triaged()` stamps once, so the
figure cannot report the tool getting faster the more a row is fiddled with.

**A location that is not set up never blocks a submission.** The form takes it
as typed and the queue says it matched nothing. A form that turns away a
location manager who is trying to send us a photograph has cost us the
photograph, and the dropdown is our housekeeping rather than theirs. A
location id from *another* client's link is refused rather than silently
re-filed — it is the one field that decides whose queue a request lands in.

**Promoting carries the ask across, both ways.** The slot records
`source_request_id` and the location it came from; the request records the
batch and slot it became and then moves with it (`sync_from_post`), forward
only, so a strategist un-approving a slot to fix a typo does not walk a
client's request back to New. Without the join a request is a dead form entry
and somebody re-reads the whole queue to work out which ones were done.
Promotion is **staff-reviewed**, which is the spec's open item 6 answered the
way this codebase answers it everywhere: a client liking a one-line title has
not approved a post. And it will not invent a plan — with no month built it
refuses and names the step, because a batch conjured to hold one request is a
month nobody chose the channels or the mix for.

**A swipe steers the mix, never the words.** `tag_weight()` is
`liked / (liked + passed + 1)` — one line, reproducible in a reader's head
from the two counts printed beside it, and it only ever decides which *kinds*
of post get offered next. That is what makes it safe to be this crude. A tag
the client asked for on the preference form outranks the weighting, because
that is the one signal that was not inferred; a fixed share of each batch goes
to tags nobody has answered on, or the mix converges on whatever they liked
first and stops being able to learn anything. A second tap on a phone is not a
second answer.

**"We could not ask the model" is not "this business has nothing to say."** A
failed idea call still returns a batch built from the tag prompts, marked
`source: "house"`, and the screen says which it got — the rule
`modules/image_picker/profile.py` arrived at. The do-not-mention list is
**checked** against what comes back as well as being put in the prompt, the
`hub/blog_spec.py` rule: a prompt is a request, and "the model was told not
to" is not evidence that it did not.

**Nothing is pushed until the scope is granted, and that is asked rather than
discovered.** `hub/suite_accounts.publishing()` diffs
`social-media-posting.write` against what HighLevel actually granted, and it
is tri-state — granted, not granted, or **not measured** because HighLevel
omitted the scope list, which is not evidence the scope is missing and is not
permission either. Until it is granted the whole drafting pipeline still earns
its keep through the CSV under Suite's Bulk Upload, and every screen says
which of the two routes it is offering rather than drawing a push button that
fails at the moment somebody is waiting on it. The endpoints are
**transcribed** from the build spec rather than fetched, and the collection
path is one environment variable (`SOCIAL_SUITE_POST_PATH`) because nothing
has ever been able to exercise them.

**A failed push leaves the post approved.** Never `scheduled`, never
`published` — a client-approved post that quietly reads as scheduled is gone,
and the queue says it is handled. `apply_push_result()` is the one place that
guard lives. Retry is a button and never automatic: a flaky response is
exactly the case where the write may well have landed, and an automatic retry
there is a double post on somebody's page. A 200 carrying **no post id** is
refused as a success for the same reason — without an id nothing can read the
status back, and a retry would post twice.

**One post at a time, never a month in a loop.** A loop that pushes twenty and
fails on the eleventh leaves a person working out which ten landed.

**Which sub-account a client is has one answer now.** `hub/suite_accounts.py`
reads `PickerClient.ghl_location_id` — the one place in this Hub that records
it — and `modules/commercial_builder/client_link.suite_location_id` delegates
to it rather than carrying a second copy, the way
`modules/radio_promo/voices.py` re-exports `hub/voice_casting.py`. The join is
an exact domain or an exact normalised name and nothing else, and two rows
naming different sub-accounts for one business is **named, never picked
between**: posting to the wrong sub-account publishes one client's content on
another client's page, which is the worst outcome any tool here can produce.

**Three answers on a post, not two.** "Yes", "yes with my changes" and "not
this one" are the three real replies, and an approve/reject pair forces the
middle one into whichever end is nearest — `modules/ads_builder/spec.py`'s
rule. A change request **requires the words**, and it clears the approval, or
a post the client asked to change could be pushed while the request sat
unread.

**What a client sees is built server-side, not left to a template.**
`_posts_for_client()` returns the fields and only the fields: no flags, no
internal status, no strategist's name, nothing of any other client. A subset a
renderer merely happens to omit is one the next renderer prints, which is why
`modules/scans` strips its audit the same way. Only slots actually put to the
client appear — a slot somebody is still writing is not a decision anybody is
waiting on, and showing it invites a change request on a draft. **Sending
holds back a slot with a blocking flag** and says how many, because asking a
client to approve an unauthorized claim is asking them to authorize it after
the fact.

**A location manager is not a lead.** Every other capture point in this Hub
writes through `hub/leads.py`; this one deliberately does not. The person
filling in the request form works for a client we already have, and filing
them as a lead puts a live client into the sales panel as a new business.

**The agent reads and reports; it never writes and never publishes.** Its
contribution to a batch is a list of *tags*, which a client still swipes on
and a strategist still promotes. Every finding carries what was measured and
the screen it is acted on from, never a product name — the
`hub/website_audit.py` rule. Four of its five inputs can be unavailable on any
day and each has its own kind of nothing, so `signals()` answers with a state
per input and performance reads **not measured** with the reason rather than
zero. Nothing it raises is drawn red: a page of red is a page people scroll
past.

**Ideas are offered on a schedule, or the client's link opens on nothing.**
`generate()` was reachable only from a button in the staff queue, so a client
who opened their swipe link saw "Nothing to look at just yet" — for ever,
unless a strategist had remembered that week. `ideas.sweep()` runs on
`hub/scheduler.py`, hourly ticks deciding a weekly interval **per client from
that client's own last sweep** rather than from the job's schedule, so a
redeploy cannot offer two batches in a day — the `purchased_domains` shape,
for the same reason. Bounded on both axes (eight clients, three minutes)
because these are model calls sharing one scheduler thread and a call has no
useful ceiling.

Three gates on who is swept, each a way to spend a model call on nobody.
**Somebody at the client must have swiped at least once** — the first batch is
the strategist's to send, and a client who has never opened the link would
otherwise accumulate ideas nobody reads at a call a week for ever. **Their
deck must be nearly empty**, or the backlog grows faster than anyone answers
it. And **not more often than weekly**. What was skipped is named rather than
counted: "nobody was eligible" and "we could not read the client list" are
different answers.

**A client record that says nothing about the work is the failure this repo
counts six of.** Client 360 already had a Social Media card — that is their
profile URLs, a different question from whether anybody at this client is
asking us for anything. A client could have three requests overdue, a link
nobody had sent them and four posts sitting unanswered, and none of it was on
the one screen a rep opens. `hub/social_status.py` answers it, and the
dashboard scoreboard reads the same module so the two cannot disagree — the
`/api/db/structure` versus `/api/integrity` trap, where two checks asking one
question answered it differently on one panel.

**Nothing told anybody a request had arrived.** A location manager submitted
at four on a Friday and it sat in a queue only somebody who opened the tool
would ever see. There is no mailer in this Hub, so the honest route is putting
it where people already look: a scoreboard on the dashboard, above System
status, where every figure opens the rows behind it rather than the tool the
reader would then have to filter. It counts **requests only** — reading posts
awaiting approval means opening every plan file for every client on the book,
on a page that loads on every visit, and a number that costs a page load is a
number somebody turns off. The per-client card can afford that; the dashboard
cannot, and the split is stated rather than discovered.

**An empty scoreboard says which kind of empty.** "Nothing waiting" and
"nobody has been sent their link yet" render identically as a zero and only
the second is somebody's to fix, so the line says so in words beside the
figure. Same on the card: a client with no requests is told that the link may
never have gone out.

**A photograph the client sends reaches Cloudinary and not the gallery.**
That is the half of the asset pipeline that was actually missing.
`storage.put()` stores the bytes; every screen that offers "the client's own
assets first" — the planner's own image assignment, Image Creator — reads
`client_context.gallery_images()`, which reads the image picker's gallery. So
a photograph a location manager sent in was invisible to the tool built to
prefer it, while the client had been told it arrived, and nothing errored at
either end. `_file_into_gallery()` files it, labelled with the shop it came
from, and the two writes are **reported apart** — the `hub/domain_links.py`
rule, since "stored" and "stored in one of two places" are different
outcomes. Nothing branches on the gallery write: the client is watching and
their upload has already succeeded, so a gallery that will not answer costs
the composer a picture and never costs them the photograph.

The gallery row is **created** where a client has none, which is a deliberate
exception to `provisioning.py`'s "creating is asked for, not assumed" — there
the question is whether a link should exist, and here there are already bytes
from a named client on a link we sent them. It is not pushed to the Suite
media library: this is a photograph somebody sent us to consider, not
approved work.

**The canvas presets were already there.** 1080x1080, 1080x1920 and 1200x630
have been in `modules/image_creator.CANVAS_PRESETS` all along, under Social.
Worth writing down, because the obvious reading of the spec is that they need
adding and adding them again is how one tool comes to offer two Instagram
Posts of different sizes.

**The queue shipped with no explanation on it, which is how Smart 1 Ads
shipped.** `hub/help.py`, `hub-help.js` and the tour machinery all working,
and nothing on the page opting into any of it. Eight bubbles now, every one
guarded `if help_dot is defined` so a module whose Jinja environment never
got `install_template_helpers()` loses the icon rather than the page. There
is deliberately **no `data-screen`**: a tour is offered only where one is
registered, and naming one that does not exist is the same silence one step
earlier. `test_social_content.py` reads the template and requires every key
it places to resolve in the registry — a bubble whose key is missing is
removed client-side, so the template reads as helped and the page shows
nothing, and nothing anywhere reports it.

**The queue is linked from the planner, not tiled separately.** It is the
other half of one tool; a second tile is two things to keep in step and only
one of them ever gets updated. It carries `data-demo="off"` and no
`data-screen`, because the module's walkthrough was written against the
month-planning screen and offering a tour that does not exist is the silence
Smart 1 Ads shipped on Settings and Live campaigns.

`test_social_content.py` asserts all of it, including that the client's four
pages are reachable with no login and that the staff queue and its API are
not — both halves, because a client-facing page behind `AuthGuard` is a login
form in front of somebody with no account, and a staff queue outside it is
every client's name answering 200 to anyone with the URL, which is the hole
`modules/commercial_builder` shipped with.
