## Client 360's header said the same thing twice, and nothing about who to call

Client 360's header carried Salesperson and Partner under the client's name
and again, two rows down, in the strip that also holds Assigned, Client
Success and Followers. The assignment line went on to say since when and by
whom, and that it was "managed from Client Assignments" -- text nobody on the
record acted on. The business category sat in the header as a pill nobody
could change and in Client Info as a free-text field, and the two disagreed
(Buckeye Lake Winery: "General Business" above, "Winery" below). A record
with no contact said so in gray. And a source that answered 502 left a
sentence telling the person to refresh, without the button.

What changed, September 2026, one thing per line:

* **One roles row, titled Smart 1 Internal.** Partner, Salesperson,
  Assigned, Client Success, Followers on the left; a **Client Warnings**
  column on the right. The salesperson is carried across from the products
  payload (`window.__c360sales`) because `/api/client/roles` does not read
  Knack's product rows. The since/by/managed-from text is gone.
* **Client Warnings is `record_health.client360()`'s own `queue`** -- the
  list the next-action line is already picked from ("6 products ending
  within 21 days", "live products with no monthly amount") -- drawn where
  somebody reading who is on the client will see it, plus the record's own
  local finding when no contact has a name or email. `loadHealth()` stores
  the queue; `renderC360Warnings()` draws it into the row whichever of the
  two fetches lands second. Not a fourth idea of what "bad" means.
* **Refresh is a button.** `showC360RequestFailure()` and the health strip's
  unread state both offer `c360RefreshButton()`, which re-runs `run()`.
* **The category is one string, read from one place.**
  `hub.industry.display_label()` -- a person's own wording (`custom_label`),
  else the canonical label, else the typed profile text on a record the
  scan left on General. The header pill and Client Info both show it and
  both open the same editor: a dropdown of the taxonomy plus "Other, type
  your own". A custom label is resolved onto the nearest canonical key
  (`resolve()`; winery, vineyard and distillery are restaurant aliases
  now) so the Image Picker's row (`_mirror_picker`) and every module keyed
  on the taxonomy follow the change. Every industry write, the scan's or a
  person's, lands on `profile.category` too; saving a typed category calls
  `set_manual(custom_label=...)`. The scan remains the point of truth until
  somebody picks by hand, exactly as before.
* **An alias can carry its own word.** Read from the live mirror after the
  deploy, Buckeye Lake Winery Inc still said General Business: its record
  held a `general` reading from the day before, and `due_for_resweep()`
  kept any reading for 30 days. Two rules followed. A `general` reading is
  not a reading and is asked again every night. And `ALIAS_LABELS` lets an
  alias whose own word is what a person calls the business (winery,
  vineyard, distillery, brewery) ride along as the record's label, so the
  name tier files the winery under restaurant for the Image Picker and the
  record reads "Winery" without anybody typing it. A manual pick of a
  canonical key clears that wording, as it clears any custom label.
  And the job that applies it, `industry_resolve`, now runs ahead of the
  slow provider sweeps in `hub/scheduler.JOBS` beside the QA jobs: it
  takes a quarter of a second reading the Hub's own disk, and behind the
  28-minute Google sweep it never got a turn on an afternoon with six
  deploys in an hour, each boot restarting the pass. Merged and deployed at
  17:15, the fix was still not on the record at 19:20. `qb_contacts` moved
  with it for the same reason.
* **The scan's screenshots** sit beside the name, from
  `scan_facts.screenshots()` via `/api/client/screenshots`. A thumbnail is
  added only after its image loads, so a capture the scan host no longer
  serves is nothing on the page. Click opens a lightbox.
* **Contacts carry a level** -- `hub.seo.CONTACT_ROLES`: communicate, owner,
  accounting, reporting, consultant, do not email, other -- chosen from a
  dropdown whose first row is "Make primary". `clean_contacts()` keeps
  exactly one primary. Rows also carry `source`/`source_id` so a sync can
  find its own row again.
* **The QuickBooks billing contact is filed under Accounting** by
  `hub/qb_contacts.py`: a button on the strip, and the `qb_contacts`
  scheduler job every Sunday at 2pm Eastern (`QB_CONTACTS_SYNC_HOUR`
  overrides the hour). Never over a typed value; a name is not required; a
  customer with nothing to file is skipped; a refusal ends the pass.
* Create display ads and Email client & history are buttons; the Creative
  Information table's third column has room between its two controls;
  Work & requests sits directly under Overview in the rail.

`test_client360_layout.py` drives the warnings column and the roles row in
node; `test_qb_contacts.py` holds the sync; `test_industry.py` holds the
custom label and the profile sync.

## The quick wins that followed, same day

* **No `alert()` boxes.** Every "that did not save" and "sent" goes through
  `c360Notice(text, kind)`: a dismissable corner card (`err` stays 12
  seconds, `ok` and `info` six) that never blocks the page.
  `c360NoticeHtml()` is pure and driven in node by the layout test.
* **No page reloads.** QuickBooks attach and detach re-run the Invoices
  card through `loadQbCard()`; a website attach and a group change re-run
  the record through `c360Refresh()`, which keeps the open section. The
  one `location.reload()` left is that function's fallback with no client
  in the search box.
* **Real buttons.** Forty-eight controls were anchors with a pointer cursor
  and no href, unreachable from the keyboard. They are `<button
  type="button">` with the same class; the template's CSS gives the button
  forms the shared styling. The layout test refuses a new one.
* **A `tel:` link** on the primary contact's phone, beside the `mailto:`.
* **The email contact line** sits in the Client Info strip
  (`#c360EmailInline`, painted by `paintEmailLine()` from
  `window.__c360emailLine`) instead of as its own gray sentence above the
  health strip; the detail rides in the tooltip and the text opens Email
  client & history.

## The bigger ones, same day

* **Overview is eight cards, not thirteen.** Products & IOs, Orders we
  have sent, Coming up, Ad performance, Smart 1 Suite Account, Pipeline &
  leads, Proposals, Client Notes. Landing pages went to Website & audits,
  Google listing to Google & traffic, YouTube channel and Email campaigns
  to Social & links, Target audience to Creative & brand. The layout test's
  grouping table is the record of where each card lands.
* **Rail badges.** `c360WarningCounts()` counts the health queue's and the
  record's own findings per rail section, worst level winning, and
  `paintRailBadges()` draws the count on the rail item, red for `bad`. A
  finding with an `href` goes to another record and counts nowhere here.
* **An Ends column on Products & IOs**, with the end date and an amber
  pill inside the same 21-day window `hub/record_health.py` uses (a
  constant the test holds equal to `ENDING_SOON_DAYS`), red when Knack
  still calls an ended product live. Both of Knack's date spellings are
  read, ISO from the live pull and m/d/Y from the committed export.
* **The site score beside the screenshots**, from
  `scan_facts.screenshots()`, which carries `score` and `tier` whenever
  there is a scan, captures or not; it links to the audit.
* **The latest note pinned under the Smart 1 Internal row**, one line with
  the author and date, opening the notes card on a click.

