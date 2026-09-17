## Verifying a change

Booting the app catches what static analysis misses; several serious bugs were
only found by running it.

```bash
python3 -c "import ast,pathlib; [ast.parse(p.read_text(errors='ignore')) \
  for p in pathlib.Path('.').rglob('*.py') if '_attic' not in p.parts]"
python tools/jscheck.py            # every .js file and inline block, via node
python tools/checktemplates.py     # the Jinja-carrying blocks jscheck skips
python tools/linkcheck.py          # every internal URL resolves, every url_for has a route
python tools/pagecheck.py          # the page the browser actually receives
python tools/menucheck.py          # every link the menu emits, and the anchors it aims at
python tools/integritycheck.py     # known defect patterns
python tools/spellcheck.py         # American English in everything a person reads
python3 test_secret_fields.py      # every box a credential is typed into masks
                                   #   it, and the check that says so still bites:
                                   #   the words it must match and the ones it
                                   #   must not, both driven
python3 tools/claudedocs.py        # the docs/claude index matches the directory:
                                   #   every file indexed, the title its own heading
                                   #   gives, the line count measured rather than
                                   #   typed, and no two files at one number
python3 test_jsonstore.py          # the mirror restores, one answer on who is outside
                                   #   it, and which database it mirrors into being a
                                   #   setting rather than a latch on the first write.
                                   #   Also the three questions about the disk -- what
                                   #   JSON has no mirror, what opens a database, and
                                   #   what writes bytes -- each cross-checked against
                                   #   a cruder second reading so an empty answer
                                   #   cannot mean the scan broke; and the opposite
                                   #   question about those same files -- a credential
                                   #   sitting in a mirrored store as a readable
                                   #   string, which every other check here passed
                                   #   while it was true
python3 test_jsonstore_locking.py  # two real processes with two real data roots:
                                   #   a flock each instance takes on its own disk
                                   #   serialises nothing, and the read half of a
                                   #   read-modify-write has to come from the mirror
                                   #   too -- both halves confirmed red on their own
python3 test_audit_store.py        # the activity log is a table now: AUDIT_LOG_PATH
                                   #   names the legacy file and selects nothing, the
                                   #   database is not gated on Postgres or no test
                                   #   would exercise it, the history is carried
                                   #   across once, and an outage's rows are read back
python3 test_db_boot.py            # a database blip at boot is not a verdict for
                                   #   the life of the worker, and sign-in says
                                   #   so in words rather than answering 500
python3 test_scheduler_health.py   # the jobs working, not just the loop alive:
                                   #   overdue, failure streaks, and the worker
                                   #   that cannot see the timings; and the
                                   #   integrity sweep that runs the defect
                                   #   checks HERE -- transitions rather than a
                                   #   heartbeat, and no row carrying the
                                   #   credential it was written to find
python3 test_smartforecast.py      # weather lifecycle rules, immutable history,
                                   #   public embeds and Render disk recovery
python3 test_smartforecast_store.py # the move off its own SQLite file: a forward
                                   #   foreign key SQLite resolves lazily and
                                   #   Postgres refuses, a generated-id sequence
                                   #   that does not advance on the seed's explicit
                                   #   ids, and a SELECT alias in HAVING -- all
                                   #   three invisible on SQLite by construction
python3 test_report_cache.py       # one run per report per day; a failed run is never
                                   #   the answer, and a write drops what it changed
python3 test_qa_reports.py         # every report on /qa answers and is drawable,
                                   #   and one that could not look never renders
                                   #   as "all clear"
python3 test_ads_module.py         # Smart 1 Ads: the Ads Editor handoff, the client join
python3 test_ads_estimate.py       # the estimate a client reads, and what they can answer
python3 test_ads_keyword_plan.py   # measured CPC, the access tier, the deploy preflight
python3 test_ads_explainer.py      # the bubbles, the per-screen tour, the walkthroughs
python3 test_ads_account_reads.py  # how the Ads Builder reads its live accounts:
                                   #   uncapped and by key. A cap spent on
                                   #   PROPOSALS is a cap of roughly half as
                                   #   many accounts, and past it a scheduled
                                   #   client report went out with a blank
                                   #   client name. Same defect class as
                                   #   test_reports_map_reads.py, same guard
python3 test_help_layer.py         # every bubble placed has help behind it, both
                                   #   ways one is placed, a key built at runtime
                                   #   named rather than guessed at, the
                                   #   walkthrough saying which step it cannot
                                   #   run, a selector that tests nothing
                                   #   clearing no floor, and coverage measured
                                   #   against the tiles rather than a list
                                   #   that went stale
python3 test_target_areas.py       # target areas, delivery, the Suite push
python3 test_lead_delivery.py      # one write path per lead, the hourly sweep
                                   #   that finally drains the queue, and a
                                   #   store that survives being rewritten
                                   #   while the other worker takes traffic
python3 test_lead_store.py         # where a lead actually is: the table, the
                                   #   pending file when the database will not
                                   #   answer, and the one-time import that does
                                   #   not mark itself done unless every lead in
                                   #   the file is in the table
python3 test_google_token_store.py # the OAuth refresh tokens off their SQLite
                                   #   file: moved as ciphertext, never through
                                   #   _fernet(); ids preserved because an alert
                                   #   points at a report; and the sequences moved
                                   #   with them, which counting alone misses
python3 test_io_delivery_lock.py   # the reservation that keeps a duplicate
                                   #   opportunity out of Smart 1 Suite: the lock
                                   #   is asked for with a try-lock rather than
                                   #   waited on, so a broken one fails in a
                                   #   second instead of hanging the job
python3 test_storage_fallback.py   # what the shared uploader hands back with no
                                   #   Cloudinary: the bytes are kept, but the
                                   #   URL is empty rather than the /hub/assets
                                   #   path nothing has ever served -- asserted
                                   #   by asking the booted app to route it
python3 test_check_reconciliation.py # matching and the QBO payment payload, and
                                   #   where the state lives: the QuickBooks
                                   #   tokens, payer aliases, records and audit
                                   #   trail through jsonstore, under the lock a
                                   #   threading.RLock never gave it across the
                                   #   second gunicorn worker
python3 test_scan_widgets.py       # widget placements: leads counted, pause/edit/delete
python3 test_scan_run.py           # what a prospect on somebody else's
                                   #   website is told: a callback token
                                   #   that survives the URL, a run that
                                   #   is over saying so rather than
                                   #   being polled to the ceiling, no
                                   #   promise of an email nothing here
                                   #   can send, and one unlock per run
python3 test_scan_lead.py          # a scanned business that is not a client
                                   #   becomes a lead: an unreadable client
                                   #   list refuses to answer rather than
                                   #   filing the client book as prospects,
                                   #   a website that is already a lead is
                                   #   linked not re-filed, and a row nobody
                                   #   can contact is refused by name
python3 test_prospect_queue.py     # who to call, in the order the work has to happen
python3 test_upsell_report.py      # what the audit says we could sell each client:
                                   #   coverage named, recorded vs observed kept apart
python3 test_prospect_record.py    # the record a scan produces: four kinds of empty on
                                   #   the Suite card, a timeline that names what it
                                   #   could not read, files, and converting
python3 test_unwired.py            # nothing is defined and left uncalled
                                   #   without a reason written down
python3 test_audit_summary.py      # the paragraphs a prospect reads: a
                                   #   measured figure re-typed is not an
                                   #   invented one, and an amount nobody
                                   #   measured is still refused
python3 test_website_audit.py      # the spend block that leads the audit, the customer
                                   #   placement, the lead every scan files, merging two
                                   #   rows that are one prospect
python3 test_prospect_explainer.py # the two screens explain themselves: every key
                                   #   resolves, every tour step rings a card the
                                   #   page draws, and none of it reaches a prospect
python3 test_detail_ui.py          # one description of the record-page look, and the
                                   #   three module screens that read from it
python3 test_magic_resize.py       # one design into the whole size set: sizes read
                                   #   from the kit rather than restated a fourth
                                   #   time, a frame the engine is unsure about
                                   #   marked rather than shipped, copy that
                                   #   reaches a hand-tuned frame and layout that
                                   #   never does, and a model that proposes
                                   #   positions rather than a picture
python3 test_menu_layout.py        # the three index pages: every tool tiled once and
                                   #   only once, and the internal calculator that
                                   #   computes the same plan and captures nothing
python3 test_qa_tasks.py           # asking somebody to check a page: four states
                                   #   rather than a done flag, a reviewer who
                                   #   cannot close their own review, an
                                   #   attachment in the database rather than on
                                   #   a disk nothing backs up, a dropdown read
                                   #   from the nav and the tiles, and a card
                                   #   that never draws a zero over a table it
                                   #   could not read
python3 test_department_views.py   # a curated shortlist per department: block
                                   #   validation, a delete that un-assigns
                                   #   rather than dangling, and the editor
                                   #   gated behind Utilities while reading
                                   #   your own view is not
python3 test_sales_status.py       # the pipeline on the dashboard: five signals,
                                   #   one reading, and counts that land on rows
python3 test_knack_map.py          # what is mapped in Knack and what is
                                   #   assumed: read from the owning modules,
                                   #   a confirmation retired when repinned
python3 test_proposal_promises.py  # what a plan promises every month, month
                                   #   by month against the work log: landed,
                                   #   marked, due, missed or not measured,
                                   #   housekeeping never raised, and a mark
                                   #   that clears the health issue on read
python3 test_proposal_kickoff.py   # whose each plan item is, following the
                                   #   client's owner unless one is named on
                                   #   the item; the kickoff document built
                                   #   from the kept plan and counting what
                                   #   is not; and the client's page at a
                                   #   stored token, carrying the fields only
python3 test_proposal_client_answers.py
                                   # the client answering on their own page:
                                   #   a proposal beside the plan and never
                                   #   in it, only their keys and only the
                                   #   offered choices, a hand-back that is
                                   #   the supply key answered smart1, one
                                   #   404 on the POST, and a pending reply
                                   #   counted wherever an open question is
python3 test_proposal_autostart.py # a won quote starting its own plan: once
                                   #   per quote, Approved and Converted only,
                                   #   a client with another run open named
                                   #   and never superseded, too old skipped
                                   #   and counted, and the run saying it was
                                   #   automatic on the plan, the event and
                                   #   the quote's own strip
python3 test_proposal_progress.py  # where each launch task and creative item
                                   #   stands: done is a press with a name on
                                   #   it, landed is read off the work log for
                                   #   the tool that makes the item and never
                                   #   stored, overdue still counts when the
                                   #   log cannot be read, and each reaches
                                   #   My Clients one issue per item
python3 test_io_reconcile.py       # the orders we sent against the campaigns
                                   #   Knack has: a stale source never reads as
                                   #   proof, a row can be settled, and the
                                   #   money a campaign is trafficked at
python3 test_io_records.py         # the order written down: one row per
                                   #   number, a resubmission that revises it,
                                   #   and bookkeeping that cannot fail a submit
python3 test_campaign_cost.py      # one number for what the campaign costs: the
                                   #   cover, the plan, the summary and the IO
python3 test_quote_validity.py     # how long a price stands, the Expired nothing
                                   #   set, what Suite may decide, and delivery
                                   #   back under the media plan
python3 test_proposal_share.py     # the client's copy: who opened it, how often,
                                   #   and an acceptance tied to one revision
python3 test_proposal_targeting.py # the coverage map, the pasted location list,
                                   #   the competitor research, and a bulleted
                                   #   list that reaches the client as a list
python3 test_proposal_consulting.py # the one line the card does not name: the
                                   #   card stays the wholesale card, the join
                                   #   is an exact string, a description is
                                   #   required because the product name is
                                   #   shared, and it survives to both the
                                   #   client's plan and the insertion order
python3 test_proposal_spec.py      # the 13-part spec, the creative gate, ROI math,
                                   #   the 2x quoted rate, the product a goal leads
                                   #   with, ZIP exceptions and what the Suite covers
python3 test_rate_card_coverage.py # every product on the card, bought on a
                                   #   proposal that renders: eight campaigns
                                   #   derived from the card's own categories,
                                   #   a ninth holding every name that means
                                   #   two products, and a new category that
                                   #   fails by name rather than being skipped
python3 test_landing_maker.py      # built pages stay public and chrome-free,
                                   #   and the card on the client's own record:
                                   #   three reads behind it, each saying when it
                                   #   could not answer, and no state drawn as 0%
python3 test_quote_numbers.py      # uploaded quotes are numbered, drafts delete
python3 test_api_usage.py          # the Google/ElevenLabs/Cloudinary estimates
python3 test_social_plan.py        # the post mix, the copy checks, the CSV
python3 test_social_content.py     # multi-location requests, the client's four
                                   #   signed pages, the idea weighting, and a
                                   #   push failure that never reads as scheduled
python3 test_web_tickets.py        # the object_107 ids, the form, what a write carries
python3 test_ad_copy.py            # the ad copy object, discovered not guessed;
                                   #   one candidate or none, nothing invented, and
                                   #   the triage control on all three forms
python3 test_campaign_support.py   # the object_121 ids, every option off the live
                                   #   object, and what a write may not contain
python3 test_campaign_assets.py    # campaigns waiting on an asset, by media partner
python3 test_stale_creative.py     # the row actions, the evergreen overlay, the login gate
python3 test_dashboard_trends.py   # the monthly readings accumulate; no card claims a comparison
python3 test_celebrations.py       # birthdays and anniversaries: what is still to come, and who is interrupted
python3 test_housekeeping.py       # warnings moved off pages nobody can act on, with the page named
python3 test_blog_publish.py       # blog taxonomy, approved topics, the CMS panels
python3 test_wordpress_publish.py  # the other publishing path: a credential
                                   #   sealed and never handed back, a rotated
                                   #   key that is a state with a fix rather
                                   #   than an absent connection, every post a
                                   #   draft, a post tripping the client's own
                                   #   never-mention list refused by name, a
                                   #   category matched exactly or created, and
                                   #   two pages wanting two alts on one image
                                   #   named rather than last-one-wins
python3 test_site_login.py         # the client's own website login, sealed:
                                   #   the plaintext leaves the SEO record and
                                   #   the assertion is on the bytes on the
                                   #   disk, it moves exactly once, a
                                   #   deployment with no TOKEN_ENCRYPTION_KEY
                                   #   refuses to move plaintext into an
                                   #   identical file, and a rotated key reads
                                   #   as "cannot be read" rather than as no
                                   #   login on file
python3 test_outbound.py           # what this Hub may fetch: the check is
                                   #   on the resolved address rather than the
                                   #   hostname, every redirect hop is
                                   #   re-checked, unresolvable is refused
                                   #   rather than allowed, and the body is
                                   #   capped on the bytes actually read
python3 test_wordpress_schema.py   # the other half of that: one meta key
                                   #   spelled the same in Python and in PHP,
                                   #   a write read back because a 200 is not
                                   #   evidence it landed, a URL resolved by
                                   #   WordPress rather than guessed from a
                                   #   slug, nothing unapproved reaching a
                                   #   client's live site, and the plugin's
                                   #   own functions run rather than grepped
python3 test_webargs.py            # a caller's number: never a 500, never a
                                   #   negative slice, and the three call
                                   #   sites the shared helper never reached
python3 test_signing.py            # what this Hub signs with: a literal
                                   #   fallback is a forgeable admin session,
                                   #   a placeholder is set and is not a
                                   #   secret, and two readers of one salt
                                   #   that each generated their own random
                                   #   secret refused each other's cookies
python3 test_analytics_ask.py      # a GA4 comparison keyed on the tag Google
                                   #   actually sends, a time series left in
                                   #   the order it was asked for, and a total
                                   #   that says what it is the total of
python3 test_schema_questions.py   # "none" is an answer to "any awards?", one
                                   #   reading of whether a schema can be
                                   #   approved, and two sources that were
                                   #   reported as zero rather than not built
python3 test_landing_images.py     # a picture on a client's landing page is
                                   #   theirs or it is not captioned as
                                   #   theirs, and a size nobody measured is
                                   #   not a size
python3 test_blog_images.py        # one image per post rather than one per
                                   #   title, a badge that counts the posts
                                   #   the list still shows, a hero filed at
                                   #   full size saying so, and a pending
                                   #   image the audit knows is not an orphan
python3 test_seo_tasks.py          # one page, however its URL was written:
                                   #   the ticket dedupe compared the raw
                                   #   string while the title beside it was
                                   #   already canonical
python3 test_seo_intelligence.py   # the Search Console recommendation path:
                                   #   a column named query no longer hides
                                   #   Model.query, the weekly refresh lands,
                                   #   the queue answers, the page says when
                                   #   it cannot, and the job reads red when
                                   #   every refresh failed
python3 test_seo_page.py           # the SEO list and record: a pill with four
                                   #   answers, a name nobody gave, a failed
                                   #   record that is not an empty one, SEO
                                   #   work reaching the client's own page,
                                   #   two editors that keep what was typed,
                                   #   and the book read live with the source
                                   #   named on both screens
python3 test_image_tools.py        # upright photo crops/resizes, target warnings,
                                   # provider failures and free preview accounting
node test_image_optimizer_ui.js    # target warnings through download/gallery flows
python3 test_image_pdf_optimizers.py  # the two file tools: what they refuse,
                                   #   animation that survives a resize, and
                                   #   no Pillow repr or Ghostscript stderr
                                   #   reaching the person who uploaded
python3 test_utm_bg_tools.py       # the two tools no test named: a search
                                   #   whose count was the page's own length,
                                   #   a CSV that searched five of eleven
                                   #   fields and came back empty, a cut-out
                                   #   filed with dimensions measured and
                                   #   dropped, a credit cache one worker in
                                   #   two could see, and a batch the screen
                                   #   offers and the framework refused in HTML
python3 test_image_download.py     # image downloads, the shared zip builder, and the
                                   #   preview every gallery draws instead of the original
python3 test_image_audit.py        # every image attached to a client or a lead,
                                   #   a gallery you can search, and nothing
                                   #   filed under a provider nobody declared
python3 test_client360_health.py   # the derived health strip: every pill from
                                   #   the stores, a source that refuses drawn
                                   #   as its own state, and both halves of the
                                   #   blogs rule answering to one day
python3 test_client360_layout.py   # the record's cards land in their rail
                                   #   sections by name, driven in node — a
                                   #   match list that stops matching piles
                                   #   every card into Overview with the page
                                   #   still looking complete — and the four
                                   #   actions the accordion's toolbar carried
python3 test_commercial_dashboard_layout.py
                                   #   the Commercial Builder dashboard's own
                                   #   sections, and the rail label assistive
                                   #   technology reads
python3 test_client_images.py      # every module that logs client work is one the
                                   #   record can name; deleting a client image, the
                                   #   count, the one brand
                                   #   card, the contact details offered into the strip,
                                   #   the display-ads work log, and the way back
python3 test_client_uploads.py     # the client upload link, and the client an IO creates
python3 test_image_picker.py       # upload sources, deleting a gallery, the two questions, folders, the SEO copy sweep
python3 test_image_creator.py      # the "Client gallery" chip reads the real shared
                                   #   gallery, searches it, and always offers the link
                                   #   to the full one
python3 test_stock_search.py       # four sources in one search; a missing folder is not an empty one
python3 test_alt_text.py           # the alt-text scan, its clamps, the Claude prompts
python3 test_gpt_ads.py            # the 1:1 gate, the copy checks, the ad-ops ZIP
python3 test_video_library.py      # the footage index, its status row, the page's palette
python3 test_sites_match.py        # live-only matching, the name pass, a client's missing URL
python3 test_domain_links.py       # attaching a domain everywhere, orphans, renewals,
                                   #   the QuickBooks match and do-not-renew
python3 test_sites_billing.py      # hosting charges joined to sites: unbilled, and billed-but-dead
python3 test_google_links.py       # orphaned GA4/GTM/Search Console accounts
python3 test_google_access.py      # the paused Ads flow, and who an invite is for
python3 test_google_index.py       # the Google sweep: no request, and none vs cannot look
python3 test_google_inactive_qa_bulk.py
                                   # bulk actions on the inactive-accounts QA
                                   #   page: the per-row action repeated rather
                                   #   than a looser one, a failure that lands
                                   #   on its own row, a delete guarded on the
                                   #   count somebody typed, and a site check
                                   #   that refuses a GA4 row and a site nobody
                                   #   can point at, an audit endpoint that
                                   #   answers "could not read" rather than an
                                   #   empty log, and a Needs Review row the
                                   #   scan honors a skip on -- except the
                                   #   broken login, which is refused in words
                                   #   -- and a resolver that says which site
                                   #   each container would be fetched against
                                   #   without fetching any of them
node test_google_inactive_qa_bulk_ui.js
                                   # the same page's own script, run for real:
                                   #   selection per section surviving the
                                   #   re-render, a long selection sent in
                                   #   several requests at the caps the
                                   #   endpoints enforce, a delete that sends
                                   #   nothing until its count is typed, and
                                   #   the Cleanup history panel: loaded on
                                   #   open, reloaded after an action, and a
                                   #   window on the log saying it is one; and
                                   #   Needs Review offering Skip on the rows a
                                   #   skip means something for and no other;
                                   #   the bulk check collecting a site per
                                   #   container first, checking only the ones
                                   #   given an address and naming the blanks;
                                   #   and a per-section filter deciding what a
                                   #   bulk action touches -- a ticked row it
                                   #   hides leaves the selection rather than
                                   #   being deleted out of sight
python3 test_analytics_ids.py      # two names for one property are not a
                                   #   disagreement: the measurement id Knack
                                   #   holds against the property id Google
                                   #   returns, and what must keep saying
                                   #   mismatch
python3 test_msa_embed.py          # the signing page: public, chrome-free, ours to frame
python3 test_landing_embeds.py     # the gameplan embeds: framable by us, leads land
python3 test_calculator_embeds.py  # the calculator embeds: framed, public, chrome-free
python3 test_work_attribution.py   # work filed against a client reaches that
                                   #   client's record: the five keys work_log
                                   #   reads, and a table keyed on the name a
                                   #   module actually logs under
python3 test_activity_logging.py   # every module's work is attributable: an
                                   #   import is not a call, a module's own
                                   #   log() wrapper is resolved, and the
                                   #   remainder is declared with its reason
python3 test_hub_capped_reads.py   # readings in hub/ that have to be COMPLETE
                                   #   and were a window: the image audit swept
                                   #   a fifth of the archive, "this module
                                   #   never logged" was decided off the newest
                                   #   5000 rows, and a person's own inbox
                                   #   filtered a few hours of everybody's.
                                   #   Third file of the capped-read family --
                                   #   see test_reports_map_reads.py and
                                   #   test_ads_account_reads.py
python3 test_radio_builders.py     # the two radio builders: the client's own
                                   #   approval page public and chrome-free,
                                   #   nobody's trademark leaving the building,
                                   #   and a long read flagged rather than trimmed
python3 test_commercial_parity.py  # the copy a spot carries: a shared rule for
                                   #   whether the words actually say the brand,
                                   #   the address and the phone, an end card
                                   #   deleted out from under the check that read
                                   #   the client record instead, and the finished
                                   #   cut reaching the Suite once rather than twice
python3 test_radio_ads.py          # the Radio Ad Creator's second half: a bed
                                   #   composed to the spot's own length rather
                                   #   than a prompt saved and a tone played, the
                                   #   dB pair read from the one shared music
                                   #   table, the browser's WAV encoder held
                                   #   against the server's own probe in node,
                                   #   not-measured never folded into pass, an
                                   #   override that needs a reason and a name,
                                   #   and a variation that carries the scripts
                                   #   without the audio -- and Fan Radio's
                                   #   half of the same list, asserted as one
                                   #   table read twice rather than two that
                                   #   agree today; plus the playback rate that
                                   #   gets an over-long UPLOADED read back
                                   #   inside its slot, its 1.15x ceiling, and
                                   #   the rate being recorded on the mix rather
                                   #   than inferred later -- asserted in both
                                   #   builders, and asserted as the SAME
                                   #   function answering rather than two that
                                   #   agree today
python3 test_radio_parity.py       # Radio Promo's half of that list: the :10
                                   #   and the :60 that were unbuildable, the
                                   #   cost note said at pick time rather than
                                   #   after the read exists, the beats moved
                                   #   out of the prompt's own prose, and a
                                   #   named script panel run on the copy --
                                   #   where certainty rather than severity
                                   #   decides what may refuse a billed record
python3 test_fan_radio_suite.py    # Fan Radio's finished work reaching Smart 1
                                   #   Suite -- the last recorded-nowhere
                                   #   difference between the two builders.
                                   #   Through hub/suite_opportunity, not a
                                   #   third GHL webhook (asserted off the AST,
                                   #   because the module explains the old hook
                                   #   in prose); what the CLIENT approved on
                                   #   the share page rather than a staff
                                   #   press; never the naked read behind an
                                   #   unrendered mix; never a URL nobody can
                                   #   open; and a second press revising one
                                   #   opportunity rather than opening two
python3 test_radio_presets.py      # the reusable-read library: one store both
                                   #   builders offer rather than a second copy
                                   #   in each, the {business} placeholder
                                   #   filled on the way out and put back on
                                   #   the way in so the library survives its
                                   #   own first save, a preset saved under the
                                   #   old per-tool file still offered, and the
                                   #   screen asserted as well as the route --
                                   #   this was four reads, a store, a route
                                   #   and a test, reachable from no page
python3 test_radio_feature_parity.py # Fan Radio's half, which is the other
                                   #   direction: every item above landed in
                                   #   the Radio Ad Creator and none of them
                                   #   here, so this tool had two of the nine
                                   #   checks and no way to know a :30 never
                                   #   said the address. The panel is
                                   #   hub/radio_script_qc.py and both read
                                   #   it, asserted as one table read twice
                                   #   rather than two that agree today, plus
                                   #   the trademark and post-game rows that
                                   #   are genuinely this tool's own, and a
                                   #   :10 neither asked for a response nor
                                   #   judged for leaving one out
python3 test_commercial_heygen.py  # the spokesperson clip actually arrives
python3 test_commercial_providers.py # a key that was added is read, and works
python3 test_commercial_meter.py   # every billed call records, no invented price,
                                   #   and a library of what was actually delivered
python3 test_commercial_audio.py   # generated sound effects and music: a published
                                   #   limit refused by name rather than clamped, a
                                   #   retry that cannot re-spend on either worker, a
                                   #   length derived or not measured, a generation
                                   #   counted apart from a character of speech, and
                                   #   an effect capped to the shot it sits on
python3 test_commercial_library.py # what a spot is versus how it is made, the
                                   #   twelve archetypes and what each one needs
python3 test_commercial_compliance.py # which published rules a spot engages, whose
                                   #   they are, and the acknowledgment before filing
python3 test_commercial_mock.py    # the mark that says a provider is not live:
                                   #   named where the work is rather than as a chip
                                   #   on another screen, only for routes that really
                                   #   report it, and never on the client's page
python3 test_commercial_review.py  # the client's review link: public and chrome-free,
                                   #   three answers, the strictest one wins, a
                                   #   refusal that stops a delivery, and the
                                   #   answers reaching the dashboard
python3 test_hyperframes.py        # the sidecar renderer and its two skills:
                                   #   configured is not reachable is not
                                   #   working, a mock that is never filed, a
                                   #   beat list validated rather than trusted,
                                   #   a per-beat cap that holds inside the
                                   #   window, and a Vox explainer refused
                                   #   where nobody sells the slot
python3 test_job_notify.py         # the cross-Hub "it's done, come back" pointer:
                                   #   per-owner isolation, the sweep never
                                   #   touching a running job, the route
                                   #   behind the login, and submitting and
                                   #   polling a real HyperFrames render
                                   #   actually writing and moving one
python3 test_commercial_wizard.py  # the seven steps, the batch an approval opens,
                                   #   the client join, the spec check,
                                   #   the QR destination and who owns the scan; the :06,
                                   #   shots inside beats with their grammar, the published
                                   #   thresholds and whose each is, and the Amazon warning
python3 test_commercial_explainer.py # the bubbles, the per-screen tours, and a
                                   #   walkthrough that drives the page it is on
python3 test_io_start.py           # starting an IO from a proposal, a client or a file
python3 test_io_builder.py         # the IO Builder's own model calls: one reader,
                                   #   the hosted tool opt-in, an answer cut short
                                   #   named as that, the landing page read rather
                                   #   than imagined, and a refused order that is
                                   #   not filed as an order
python3 test_drafts.py             # interrupted work: the IO's server draft and
                                   #   its list, the proposal reopening where it
                                   #   was left, and a cap that names what it drops
python3 test_landing_spec.py       # what a landing page is for, and what it sells
python3 test_client_groups.py      # grouped clients: what merges, what must not double
python3 test_client_owners.py      # whose client is this, and what is outstanding
                                   #   on them: one owner, a partner selected
                                   #   rather than stored as a rule, marks
                                   #   applied on read, and a source that could
                                   #   not be read named rather than counted
                                   #   as nothing
python3 test_ghl_scopes.py         # the Suite app's scopes, and the granted-vs-requested diff
python3 test_ghl_blog.py           # a client's llms.txt published to their
                                   #   own sub-account rather than the
                                   #   agency's blog, a duplicate guard that
                                   #   says when it could not look, and the
                                   #   address Suite actually assigned
python3 test_write_attribution.py   # every write into a client's own account
                                   #   has a name against it: in both modules
                                   #   the creating half of a pair was the half
                                   #   left out, and the remainder is declared
                                   #   rather than left as an absence
python3 test_suite_panel.py        # creating and deleting Suite sub-accounts:
                                   #   a claim taken before the work and shared
                                   #   between workers, a duplicate check that
                                   #   says when it could not look, and a
                                   #   deletion recorded against the account it
                                   #   deleted rather than the name typed at it
python3 test_suite_embed.py        # Hub pages framed in Suite: the cookie, the chrome, who may frame
python3 test_suite_sso.py          # the client half: the location id is the
                                   #   authorization, and every way that goes wrong
python3 test_calculator_embed.py   # the media calculators framed on smart1marketing.com
python3 test_display_ads.py        # the display layouts, the build screen's contracts, and
                                   #   the animated GIF: whose rule each number is, a loop
                                   #   that can never be endless, QA on every frame, and
                                   #   one approval per file -- never the zip
python3 test_user_accounts.py      # the roster, the two levels, the crawler block, the throttle,
                                   #   and the signed-in headcount on the dashboard
python3 test_blueprint_guards.py   # nothing answers a stranger: every route the
                                   #   composed app serves -- reads and writes,
                                   #   fixed paths and the third addressed by a
                                   #   token or a slug, hub app and all
                                   #   thirty-one mounts -- probed with no
                                   #   session, against allowlists that say why
                                   #   each is public; and a walk that finds no
                                   #   mounts, or a rule it could not build a
                                   #   probe for, is a failure rather than an
                                   #   empty sweep
python3 test_env_config.py         # one setting, every name it answers to, and who logs
                                   #   and a template nothing renders, which no
                                   #   other check here can see
python3 test_knack_websites_source.py # websites live where Knack answers, the
                                   #   export where it will not, and a failed
                                   #   pull that never empties a good one
python3 test_spelling.py           # the spelling check still bites, its exemptions
                                   #   still name real files, and it reads the one
                                   #   module that is not Python
python3 test_claude_docs_index.py  # ...and the docs index check still bites: it
                                   #   runs against a clean repo, so every failure
                                   #   path -- a duplicate number, a file with no
                                   #   heading, a count that drifted -- is driven
                                   #   against a throwaway tree instead
python3 test_client_prefill.py     # one client reader: what a form is offered,
                                   #   what it is never offered, and what a
                                   #   model is told about the client
python3 test_ai_callers.py         # every OpenAI call site routes through hub.ai
python3 test_client_brief.py       # what build() assembles per section, per
                                   #   audience, never Knack in this pass
python3 test_ai_injection.py       # the client brief injected at the one wrapper
python3 test_industry.py           # one taxonomy, resolve()'s matching, and
                                   #   resolve_industry()'s remaining precedence
                                   #   with no Knack tier in this pass
python3 test_industry_consumers.py # every INDUSTRIES/INDUSTRY_PACKS literal is
                                   #   canonical or in LEGACY_MAP
python3 test_client_logos.py       # a logo we found reaches the client's gallery,
                                   #   once, labeled with where it came from
python3 test_ai_proposals.py       # the model proposes, the code decides, a person
                                   #   presses: project names, client photos, ticket type
python3 test_thinking.py           # the mark that says a scan or a model is running:
                                   #   one implementation, three kinds, both halves
                                   #   of the app, nothing claiming a result, and
                                   #   the three inline copies held in step
python3 test_llms_hosting.py       # a client's llms.txt: robots per user-agent
                                   #   group, the header that would have said
                                   #   noai, the 301 the redirect has to be,
                                   #   and a robots we could not reach that is
                                   #   never read as permission
python3 test_search.py             # the top box: a client the query names comes
                                   #   first, and every screen is findable
python3 test_oauth_redirects.py    # every OAuth callback, the hostname each is
                                   #   built from, and — the half nothing
                                   #   asserted — that the code sends the
                                   #   string the panel tells you to register
python3 test_ghl_oauth.py          # the Suite install: a refresh that keeps
                                   #   the token it was not given, a disconnect
                                   #   that does not undo itself, a rotated key
                                   #   that reads as re-consent rather than a
                                   #   crash, and a status carrying no secret
python3 test_site_blocks.py        # the website blocks a page is built from
python3 test_hub_help_layer.py    # the hub's own tours: offered at all,
                                   #   and a walkthrough button only where a
                                   #   scenario is written for that page --
                                   #   swept across every hub page, by a sweep
                                   #   that does not sign itself out partway
python3 test_linkcheck_helpers.py # the URLs linkcheck could not see: a
                                   #   module's own request helper, and
                                   #   sendBeacon; and prose is not a
                                   #   call site
python3 test_reports_store.py      # the ad-report fact table and what sits beside it
python3 test_reports_pages.py      # every /reports screen refused and served
python3 test_reports_ttd.py        # the native Trade Desk pull and the MyReports CSV
python3 test_reports_google_perf.py # the native Google Ads pull
python3 test_reports_stackadapt.py # the native StackAdapt pull
python3 test_reports_bing.py       # the native Microsoft Ads pull and the
                                   #   connection behind it: one consent kept
                                   #   like Google's, four headers on every
                                   #   call and none of them leaving, one
                                   #   report per pull polled inside the
                                   #   budget, and the Settings card
python3 test_reports_audiogo.py    # the config-driven AudioGo pull and its check page
python3 test_reports_groundtruth.py # the GroundTruth pull, whose key arrived
                                   #   before the document: placeholders a
                                   #   person confirms, the key sent nowhere
                                   #   until the origin is named, visits under
                                   #   their own name and on the client's page
python3 test_reports_amazon_dsp.py # the native Amazon DSP pull and the
                                   #   connection behind it: five claims told
                                   #   apart rather than one "connected", one
                                   #   report per advertiser polled inside the
                                   #   budget with pending carried between
                                   #   ticks, the pre-signed download fetched
                                   #   with no Authorization header on it, and
                                   #   purchases kept out of conversions
python3 test_reports_seo.py        # the organic search section for SEO clients
python3 test_places.py             # a client's Google listing: proposed once,
                                   #   confirmed by a person, read once a night,
                                   #   and a rating never printed without its
                                   #   review count
python3 test_youtube.py            # a client's YouTube channel: a link read
                                   #   for one unit before a search spends a
                                   #   hundred, one proposal or none, a hidden
                                   #   subscriber count never a zero, the key
                                   #   saying which variable answered, and the
                                   #   client's page gated on a video or social
                                   #   product AND a confirmed, read channel
python3 test_suite_email_stats.py   # a client's email campaigns read from their
                                   #   own Suite sub-account: two scopes, the
                                   #   campaign list with the statistics
                                   #   endpoint filling gaps under a cap, five
                                   #   kinds of nothing drawn apart, the nightly
                                   #   sweep held back while a scope is missing,
                                   #   and the client's report gated on a live
                                   #   email product AND a linked, read account
python3 test_reports_normalize.py  # the provider normalize, provider check and auto-mapper
python3 test_reports_public.py     # the client's live report page, its link and the spend rule
python3 test_reports_crossover.py  # the product-level crossover and the forbidden-word sweep
python3 test_reports_pacing.py     # the pacing board and the cost report
python3 test_reports_health.py     # feed health drawn three ways, and the day the
                                   #   figures run through
python3 test_reports_confirmations.py # a campaign filed from its name waiting for a
                                   #   person, and a provider map read only once confirmed
python3 test_reports_quarantine.py # a fact row that cannot be true held, not filed
python3 test_reports_reconcile.py  # each platform's month against the platform's own
                                   #   total, independent or re-read, through yesterday
python3 test_reports_exec_summary.py # the summary on a client's dashboard: staff
                                   #   generate it, staff read it, staff save it, and
                                   #   the client's page renders it and can reach no
                                   #   AI call at all
python3 test_reports_map_reads.py  # how the campaign map is read: by client, by
                                   #   campaign key, and never by sweeping a capped
                                   #   global list. Reproduces the truncation itself,
                                   #   and guards every filtering reader against
                                   #   reaching mapped_campaigns() again
                                   #   (checks.yml runs all of these a second time
                                   #   against Postgres, through _reports_testdb.py)
python3 test_periods.py            # the named reporting windows, so no model writes a
                                   #   date: every period on a fixed today, the quarter
                                   #   boundaries, the leap day, and custom's refusals
python3 test_reports_flags.py      # the deterministic flag rules, asserted from BOTH
                                   #   sides of every threshold -- 14.9% raises nothing,
                                   #   15.1% raises one
python3 test_v2_performance.py     # the ad-performance read Ask SmartHub answers from:
                                   #   two spellings summed once, pending spend excluded,
                                   #   and pacing equal to the board field for field
python3 test_v2_insights.py        # the GA4 breakdown and the optimization-sweep read
python3 test_ask_recipes.py        # the recipe library and every placement its chips
                                   #   appear in, through wsgi.application so a mounted
                                   #   page's chips are actually checked
python3 test_ci_gate.py            # the gate runs every check a person runs
```

### One command: `python3 tools/preflight.py`

```bash
python3 tools/preflight.py            # the sweep, plus the gateway's own tests
python3 tools/preflight.py --merge    # ...and the three merge-only checks
python3 tools/preflight.py --only-merge   # just those three, after resolving
python3 tools/preflight.py --list     # what it would run, and nothing else
```

It exits with the number of failing checks, so `&&` works, and it runs the
same tools in the same order as the list above rather than reimplementing any
of them. It exists because "I ran the checks" has to mean the same thing
twice: a sweep somebody assembles by hand is a sweep with whatever they forgot
missing from it, and all three `--merge` checks below are here because
something got pushed past exactly that.

**`--merge` is three checks, each earned:**

* **Conflict markers across the whole INDEX** (`git ls-files`, not a directory
  walk). `git add -A` after a merge stages a conflicted file exactly as it
  sits, markers and all, and marks it resolved. A grep scoped to the
  directories somebody expected markers in is a grep with a hole in it -- that
  is how markers reached `.github/workflows/checks.yml`.

* **Every workflow parses as YAML.** A workflow that does not parse fails
  INSTANTLY with no jobs and zero duration, and GitHub shows the file path
  instead of the workflow's name. On a pull request that reads like the
  workflow simply not being required: `smoke` and CodeQL went green while the
  entire `checks` workflow had never run.

* **Every workflow file-loop is executed with its command stubbed.** A lost
  line-continuation inside a `run:` block is still valid YAML. It parses, the
  job starts, and it silently iterates a shorter list than it names. Counting
  the filenames in the source and counting what the shell actually iterates
  are different questions, and only the second is the answer.

### The MCP gateway's tests are not in the sweep above

`mcp_gateway/` has its own tests and its own workflow
(`.github/workflows/mcp-gateway.yml`, the **smoke** check). They are
`unittest` modules rather than root-level `test_*.py` scripts, so nothing in
the list above runs them, `test_ci_gate.py` does not govern them, and they
need the separately deployed MCP SDK the Hub's own runtime does not install:

```bash
pip install -r mcp_gateway/requirements.txt     # the SDK, once
export MCP_API_TOKEN=ci-test-token-not-for-production HUB_DATA_DIR=/tmp/smarthub-mcp-ci
python3 -m unittest mcp_gateway.test_server     # V1 identity and security boundary
python3 -m unittest mcp_gateway.test_v2         # V2 identity and connector boundary
python3 -m unittest test_ask_smarthub           # the role and tool boundary
```

**Run them after editing `mcp_gateway/v2_tools.py`, `hub/ask_smarthub.py`, or
the Ask SmartHub page or widget** — the workflow's own path filter names
exactly those files. `test_v2.ToolMetadataTests` asserts the V2 tool set as a
**closed set**: adding a tool without adding its name there fails the smoke
check, which is the point. A tool the gateway exposes and nobody wrote down
is the surface growing without anybody agreeing to it, so the fix is to add
the name, never to loosen the assertion.

The test files need no pytest and no new dependencies; each runs against a
temporary data directory and a throwaway SQLite database, so none of them
touches `/var/data` or the real one.

**All of this runs on every pull request** — `.github/workflows/checks.yml`,
the single gate. CI runs the same scripts a person runs, so a green run means
the same thing in both places and no check exists only where nobody can
reproduce it.

**And that sentence was not true, in the file that makes it.** Seven of the
files this list names were run by nobody but somebody who thought to type
them: `test_unwired.py`, `test_thinking.py`, `test_menu_layout.py`,
`test_detail_ui.py`, `test_ai_proposals.py` and the two explainer files. What
they hold is not marginal — that nothing is declared and left unwired, that
one tool is tiled once and its trail names it, that four copies of the wait
mark agree, that the three places a model proposes carry no route to a write.
An eighth, `test_site_blocks.py`, was in neither this list nor the workflow,
which is the same gap one step further on. Every one of them passed; they were
simply gated by nobody, and the list saying otherwise is what stopped anybody
noticing — a sweep that has quietly stopped sweeping, reporting a clean bill of
health about the part it still covers.

The claim is **asserted** now rather than made. `test_ci_gate.py` reads the
workflow and holds it to this list in **both** directions: a `test_*.py` in
the repo that no step invokes, and a step naming a file that is not here —
which runs nothing at all. Several steps are deliberately written
`if [ -f x ]; then … else echo "not on this branch"`, so that second half
reads the guard rather than the filename, or it would report the thing that
keeps this workflow mergeable on an older branch. `EXEMPT` is the way out and
carries its reason, and it is **empty**, which is the only way this was worth
adding.

Two workflows briefly existed: `checks.yml` and a `ci.yml` written in parallel
on another branch, overlapping on `jscheck` and `linkcheck` and each carrying
steps the other lacked. They are folded into `checks.yml` — the union, not the
intersection: the four test files and the composed-app boot from one, and
`checktemplates`, `pagecheck --strict` and `integritycheck` from the other.
Two gates disagreeing about what "green" means is worse than either alone.

It runs against a real Postgres rather than SQLite because Sites Admin refuses
to start without one and serves the 503 fallback instead: on SQLite a whole
module drops out of every check that boots the app, and nothing says so.

**A setting that was right about a thing that had never happened — and then
started happening, quietly, on the side nobody was watching.** This section
used to say Render had never once deployed smart1-hub by itself: every deploy
in its history was trigger `manual` or `api`, and no service in the workspace
had a single `new_commit` in it. The diagnosis was the repo path — the service
was still pointed at the pre-transfer `smart1marketing/smarthub` — and the fix
named was reconnecting the repository under the org.

That reconnect happened, and Render's own auto-deploy has shipped every push
to `main` since: `autoDeployTrigger: commit`, deploy history nothing but
`trigger: new_commit`, each one live within a minute of the merge. The failure
this section spent a page describing is gone, and the only reason it took a
direct API check to notice is that a working auto-deploy and a broken one look
identical from the GitHub side, because neither is watched from there.

**The route around it outlived the thing it routed around.** A `deploy` job
was added to `checks.yml` while the webhook was dead — it posted to
`RENDER_DEPLOY_HOOK_URL`, pinned to the commit's own sha rather than a bare
hook, and refused rather than passing when the secret was unset. Every part of
that reasoning was right. What made it worth removing is that the secret was
**never set**, so the job had never once deployed anything: it was a second
deploy path that had only ever refused, insuring a webhook that had since
started working. And the refusal was the **sole reason main read red on every
merge** — the test job passed 171 of 171 while the run showed a red X, which
is the permanently-red gate this file names as the check people learn to skip
past. Insurance nobody has checked works is not insurance; insurance that
makes the alarm ring every day is worse than none, because it trains everyone
to ignore the alarm.

**That left a deploy which did not wait for the tests, and it has since been
closed.** Render's trigger was `commit` rather than `checksPass`, so a merge
shipped the moment it built. Measured on one afternoon, `ebb8f0c` went live at
22:08 and its CI finished at 22:19 — production had the commit eleven minutes
before the tests were done — and the merge after it was live twenty-three
seconds after the push, before CI had started at all. Both happened to pass,
which is the only reason it read as fine: **"main is green" was not protecting
production**, because production was not waiting for it.

The dashboard switch is now *After CI checks pass* — the Render API reports
`autoDeployTrigger: checksPass` on smart1-hub as of 16 September 2026, checked
against the live service rather than against `render.yaml`, which is the whole
point of the paragraph below. So green main protects production again, at the
cost of a build's wait per deploy. **Verify it against the API rather than the
blueprint before relying on it**: this is a dashboard setting on a service the
blueprint does not own, so it can be changed back by anybody with the
dashboard, silently, and the file would go on saying otherwise.

**`render.yaml` declares `autoDeployTrigger: checksPass`, and that does not
make it so.** smart1-hub was not created from that blueprint and takes its
settings from the dashboard, which the `OUTPUT_DIR` note at the foot of that
file already records having learned the hard way -- an entry that "never
reached it" while the boot log read the wrong path for weeks. So the key is
there for a service created *from* the file, and as somewhere the intended
setting is written down; the live one is still a dashboard switch. Alongside
it, `plan:` said `starter` while the service ran `2c-4g`, so a sync performed
to apply that one-line guard would have **quietly downgraded production** --
each half internally consistent, only the join between them wrong, which is
the shape this file counts a dozen of. Both are corrected together, because
correcting either alone is what makes the other dangerous.

**And `checksPass` turned the gate's own cancellation rule into a deploy
freeze.** `checks.yml` set `cancel-in-progress: true` on a group of
`checks-${{ github.ref }}` -- written for pull requests, where nobody wants the
verdict on a commit the author has already replaced. A push to `main` shared
that group, so **every merge cancelled the run for the merge before it**, and
with merges landing faster than a run takes (~15-18 minutes) `main` stopped
completing runs at all. A cancelled run is not a passed one, so nothing
auto-deployed.

Measured on 16 September 2026 rather than inferred: the last deploy with
`trigger: "new_commit"` was `11be225`, which is *exactly* the last commit on
`main` whose run completed instead of being cancelled. Twelve merges later the
service was still serving that code, moved on only by one `trigger: "manual"`
deploy somebody pressed. Every screen said green the whole time, because every
one of those merges really had passed on its own pull request.

The second cost is the one that outlives the deploy: **a cancelled run on
`main` is a verdict thrown away.** Every commit there is a different tree --
the merge result -- that nothing else will ever check, which is how the
`/qa-inactive/` breakage above sat on `main` with no completed run saying so. A
push gets a group of its own per commit now (`github.sha` in the group name, for
`push` only), so nothing cancels it. Not `cancel-in-progress: false` on the
shared group: that would have queued every merge behind one lane instead of
running them alongside each other.

`test_ci_gate.py` asserts what is true now instead of what the job used to
promise: **this workflow holds no credential at all** — no stored secret, and
no second job carrying one. Everything the gate runs, a contributor runs on a
fresh checkout. Adding a secret-holding job back is then a decision somebody
makes rather than a drift nobody notices, because it turns that check red. Its
own first draft, back when it asserted the deploy job's refusal, could not fail
— the window it searched for an `exit 1` reached a second one further down the
step, so a branch changed to echo and carry on still passed. The assertion that
cannot fail, in the file written about checks that cannot fail.

`tools/linkcheck.py` boots the composed app and checks every internal URL
literal against the route table of whichever app owns that path — and every
`url_for('name')` against the endpoints of whichever app renders that template, so it catches
the mount trap above — a module page written as `fetch("/api/lead")` works
standalone and 404s under a mount. It exits non-zero, so it can gate a
release. **Run it after touching any module template**: that one bug was live
on seven landing pages for two days, and it took down the lead capture on all
of them without anything looking wrong.

`tools/pagecheck.py` asks a different question: not what the template says,
but what the browser receives *after* `HubBar` and the hub's `after_request`
have rewritten the response. Both inject the sidebar and five script tags into
HTML they did not write, and injecting into the wrong place breaks the page
while leaving every template valid and every link resolving. That is not
hypothetical — HubBar injected at the FIRST `</body>` in the response, and the
IO Builder builds two printable documents as JavaScript template literals that
each carry their own `</body>`, so the sidebar landed inside a string, closed
the page's script early, and the entire tool rendered blank. It checks that
the chrome arrives as an *element* (`html.parser` goes raw-text inside
`<script>` exactly as a browser does, so chrome hidden in a literal is not
seen) and that every browser-delimited script block still parses.

`tools/menucheck.py` covers the gap between those two. linkcheck reads URL
literals out of files; pagecheck walks a hand-maintained list of pages. Neither
asks the navigation what it is actually putting in front of a person, and the
sidebar builds its hrefs from `LEAVES` in `hub/sidebar.py` rather than from URL
literals in a template — so a row whose route moved is data nothing reads, and
`department_tiles()` drops a key missing from `LEAVES` on purpose rather than
raising, because the nav must never break a page. This asks the nav for its own
links and follows every one: each department index, each leaf, each tile.

It also checks the half nothing else can see — every flyout group heading links
to `/views/<slug>#<anchor>`, and a fragment matching no element on the page is
invisible to every other check here: the link resolves, the page returns 200,
and the browser silently stays where it is. Both halves were confirmed red
first, against a leaf pointed at a route that does not exist and against the
heading rendering without its `id`.

Three non-2xx answers are gates working rather than defects, and `EXPECTED` in
that file names each with its reason so a fourth is a finding rather than noise
somebody learns to scroll past: Check Reconciliation's allowlist, the Users
panel needing a named Admin account, and a proxied mount whose Node process is
not running outside the container.

`tools/jscheck.py` and `tools/checktemplates.py` split the JavaScript between
them. jscheck hands every file and every inline block to `node --check`, the
real parser, but *skips* blocks containing `{% %}` or `{{ }}` because Jinja is
not JavaScript and Node would reject it for the wrong reason. checktemplates
is what checks those: it blanks the Jinja to same-width filler, so line numbers
still line up, and runs a bracket/string/template balance check over what is
left. Neither is redundant — jscheck is stricter on what it can read, and
checktemplates is the only thing that reads the rest.

`tools/integritycheck.py` runs `/api/integrity` from the command line and
fails on `high` findings. It is at **zero** — the six `medium`/`low` findings
that used to stand every run are cleared, and `provider_key_drift` is `high`
now rather than `medium`, as the note that sat beside it asked for once its
list was empty. A check that starts life red is a check somebody switches off;
one that has been green is one a new finding actually interrupts.

Then boot through `wsgi.application` (not just the hub app — that's how mount
shadowing hides) and request the pages you touched. `/api/integrity` reports
known defect patterns; `/login/health` diagnoses sign-in without a session.

`python test_reporting.py` verifies canonical Trade Desk ingestion, replacement
upserts, failure rollback, client mapping, currency separation, and admin guards.
CI also runs it on the disposable PostgreSQL service via
`REPORTING_TEST_DATABASE_URL`; never set that override to a production database.
