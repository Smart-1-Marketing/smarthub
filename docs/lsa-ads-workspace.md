# LSA Ads — Product Success

Open **Product Success → LSA Ads** (`/tools/lsa/`). This is an independent authenticated tool sharing the existing Google Ads authorization, account access and saved LSA setup records.

- **Build:** create and resume business, services, coverage, schedule and budget plans. Complete campaign creation, billing and verification in Google's setup interface.
- **Optimize:** review account recommendations and apply confirmed pause, enable and budget changes to traditional Local Services campaigns. The tool re-reads campaign membership and previous values before writing, rejects shared budgets and records successful changes in the existing activity log. Service targeting, schedules, bidding and Performance Max changes use the Google management link.
- **Monitor:** review status, spend, leads and verification history. Optional refresh runs every five minutes while the page is visible; it is not a background alert service.
- **Report:** select 7, 30 or 90 complete account-local days, print the report or download a fresh CSV. Unavailable sources remain labeled unavailable. CSV cells are protected against formula injection; exports include currency, timestamps and reporting caveats.

New campaign creation is not supported by the Google Ads API. See [Google's Local Services campaign documentation](https://developers.google.com/google-ads/api/docs/campaigns/local-service-campaigns).

The legacy Smart 1 Ads setup URL remains available and shares drafts with the separate tool. No new credentials are stored by this module. The connection is verified through an actual Google account-list request when opened; this implementation has not independently verified the newly linked production cloud app.

## Validation

Run `python test_lsa_workspace.py` for the setup, export, validation and mutation tests. The new CI step runs this suite. Tests use temporary storage and simulated Google responses and never change live campaigns.

Validation completed: 19 tests passed, plus an isolated Edge browser check of account loading, enable confirmation, budget submission, selection invalidation and desktop/mobile layout with no browser errors. Browser data and writes were simulated.

This change is in the local workspace; production deployment and live connection verification are outstanding.


## Client information form

In **Build & saved setups**, choose **Save setup & create client link**, then **Copy link** and send it to the client. The link opens a mobile-friendly form without a Hub login. Creating the link saves the current setup draft; it does not send a message automatically.

Clients provide contact details, business name/address/lead phone, website and Business Profile link, services, service areas, answering hours, proposed weekly budget/currency and optional verification-readiness notes. The form asks for no passwords, payment details or document uploads. It saves on submission and supports later updates through the same link.

Open the saved setup and select **Check client response** to read the latest answers. **Use these answers in setup** imports the setup fields after review; contact details, budget currency, address and notes remain saved with the response. Concurrent changes to either the setup or response require a refresh before import.

Links expire after 30 days. **Disable client link** revokes access while retaining the response. Creating a replacement link invalidates the previous one. Only the token-scoped form is public; setup management, responses, reports and advertising controls remain behind Hub login. Raw link tokens are not stored in the database.

Validation: `python test_lsa_intake.py` covers persistence/import, expiry/revocation, input validation, edit conflicts, token scoping, the actual AuthGuard behavior and rendered setup JavaScript.

The client-intake browser walkthrough also passed link creation, mobile submission, saved response review/import and revocation against a temporary local database. All 26 automated workspace and intake checks passed. No live Google actions or client messages were sent.
