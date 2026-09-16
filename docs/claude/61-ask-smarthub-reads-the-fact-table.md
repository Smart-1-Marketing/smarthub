# Ask SmartHub reads the fact table: named periods, decided flags, recipes

Ask SmartHub could read clients, identity, QuickBooks, GA4 mappings, proposals
and insertion orders, and could not answer *"how is Acme's search campaign
doing this month?"* -- about a Hub that already held a fact table fed by six
providers, with hourly pacing and prorated margin over the top. Three read
tools, a period convention, a flag module and a recipe library close that.

Nothing here writes. The planner still picks from a fixed catalog, Python
re-validates every call, and every read is audited under module `mcp`.

## A model asked for a date will always produce one

`hub/periods.py` exists because the planner used to be asked for ISO dates.
A model that is asked for a date produces one, and the ones it produces are
plausible rather than measured: "last month" landing on the wrong boundary, a
quarter starting in the wrong place, a range quietly including today.

So the planner picks a **name** -- `last_7`, `this_month`, `last_quarter` --
and Python turns it into days. Rules worth knowing:

* **Today is never in a window.** Today's fact row is still being written, and
  a part-day counted as a day is a drop nobody made.
* **`previous_period` is always the same LENGTH as the window it compares.** A
  31-day month against a 30-day one is a 3% move nobody caused.
* **Every window carries a human `label`**, echoed in every tool result, so an
  answer can say which days it read rather than leaving it to be assumed.
* **An unknown period is refused by name**, with the list that would work. It
  is never rounded to the nearest sensible guess.
* On the first of a month or quarter, the to-date window is yesterday alone --
  a real state, read as one day rather than as an inverted range.

`pct_change` returns `None` when there is nothing to divide by. Never zero,
never infinity: a metric that went from nothing to something did not rise by a
percentage.

## The flags are decided here, and the model may not argue

`modules/reports/flags.py` decides which movements are worth saying. A model
asked to judge a significant drop judges differently on two runs of one
question, and a figure it decided about is a figure this Hub did not measure --
`hub/audit_summary.py`'s house rule.

So the tool computes the flag and hands over a plain-English `text` to lift
verbatim. The model may explain *why* a flag fired. It may not change
*whether* one did, and it may not add flags of its own; the planner prompt and
the answer prompt both say so.

**Every rule has a floor, and the floors are the point.** A campaign whose CTR
"fell 80%" on 12 impressions has told nobody anything, and a queue full of
those is a queue nobody reads. The CTR drop needs 500 impressions on *both*
sides of the comparison; the CPA multiple needs three conversions and is
measured against the client's own blended figure rather than an outside
benchmark, because what "expensive" means here is what the rest of this
client's money is buying. `spend_no_conversions` is silent on a platform that
does not report conversions at all -- a Streaming TV buy has not failed to
convert, it was not asked to -- and the payload names which platforms counted.

The thresholds ride in every payload under `thresholds`, so an answer can
quote the rule it applied rather than asserting a number moved "a lot".

`pacing_alert` is the board's own three-day alert passed through, not
recomputed. A second engine deciding it differently is the drift
`client_view.pacing` records about its own first draft.

## `get_client_performance` is a superset of the Client 360 card, not a rival

Two readings of one question drift the day either is edited. So:

* the two-spellings reading is `client_card.candidate_keys()` -- the function
  the card itself calls -- and rows are deduplicated on their fact key, because
  a second key resolving to rows already counted doubles a client's spend;
* the `pacing` block is `pacing.compute(client=)` passed through field for
  field, with only `utilization_pct`, `band_label` and the line's own CPA added
  beside it for the flag rules;
* pricing is `pricing.client_price()`, and the billed total is `null` -- "not
  priced" -- whenever any contributing platform has no rule, because a partial
  sum is a smaller number reported as a total.

Only confirmed mappings reach a figure, which is `store.facts_for`'s rule for
every reader in the Hub. Pending campaigns are counted and named so an answer
can say what is *not* in the total. Quarantined days in the window are
reported, so it can say the figures are incomplete.

`roas` is always `null` today, with a note naming the platform. No sync writes
a conversion value; one computed from conversion counts as though they were
dollars is exactly the figure this Hub did not measure. The lookup checks the
extras blob under the names a sync would use, so it starts working the day one
of them does.

Sold is prorated **day by day**: each day a line is in flight contributes one
month-day of its monthly figure. That reproduces `pacing.cost()`'s own
arithmetic for a window inside one month and keeps working for one that spans
several. Comparing a monthly sold figure whole against a half-month of spend
inflates every margin, which `pacing.cost()` records at length.

## A recipe is a hint, never a second allowlist

`hub/ask_recipes.py` is the library of questions this data answers well. A
recipe is a question template, the tools that question is about, and rendering
guidance, kept together, and each placement is a front door to
`/ask-smarthub` rather than a second copy of the chat.

The planner still runs. The recipe's tools ride in the payload as a
*suggestion* so the four-call budget is spent on the read rather than on
working out what was meant; the catalog is unchanged and `validate_plan` still
drops anything outside it. The rendering guidance is appended to the **answer**
prompt, where it shapes a table, and never to the planner's. A recipe whose
roles cannot reach its own tools fails at import rather than offering a chip
that always errors, and a recipe a caller may not run is ignored -- the words
are the user's own either way -- with its tools and guidance never reaching
the prompt.

One URL builder, in `static/ask-recipes.js`, root-absolute: a relative one
resolves under a mounted module's prefix and 404s, the trap `CLAUDE.md` names
first. Mounted pages get their chips as plain data from their routes, because
a module's Jinja environment cannot see the hub's globals.

## The client's page renders a summary; it cannot generate one

The monthly executive summary is two presses, never one. Staff draft it --
nothing is written -- read it, edit it, and save it under their own name. The
client's page renders the saved words and can reach no AI call at all, and the
same holds for `data.json` and the PDF.

Both halves matter. A public route that could reach an AI call is a stranger
spending our credits; a paragraph about a client's results that nobody read
before it was published is the worse half of the same problem.

It is keyed by month on the report link, newest twelve kept. Saving bumps the
link's `updated_at`, which is already in the client page's cache key, so the
words reach both workers at once rather than one of them fifteen minutes
later. The 90-day view is about no single month and shows none: a summary of
September above figures for July through September does not describe them. No
summary means no section, not a sentence about why.

## What is deliberately not built

Search terms, Quality Score, device/geo/hour breakdowns and the
negative-keyword recipes wait for the Google Ads native pull to carry those
segments; `get_client_ads_findings` covers the sweep's existing findings
meanwhile. Microsoft Ads answers *not built* rather than an empty finding
list, because an empty list from a sweep that never ran reads as a clean
account.
