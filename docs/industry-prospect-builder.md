# Industry Prospect Builder

Staff page: `/sales/industry-prospects`, under **Sales → Industry Prospects**.

## What is implemented

- Persistent audiences with industry keywords, headquarters geography, employee and revenue ranges, decision-maker titles, a landing page and optional GHL owner.
- Free Apollo People API Search with page-level duplicate checks against GHL, saved Apollo contacts and this builder's purchase ledger.
- Resumable GHL suppression sync, including explicitly configured additional sub-accounts, followed by a complete saved-Apollo-contact scan. Each request advances one page. Existing records are retained conservatively, including old emails and DND history.
- Apollo bulk creation with `run_dedupe=true`. Matching contacts are not overwritten. The suppression label is applied to newly created records; existing matches remain suppressed even if Apollo leaves their existing labels unchanged.
- Server-side selection review and approval, bounded to 100 selected people, a 15-minute review expiry, and one-hour execution expiry. Phone, personal-email and waterfall enrichment are explicitly disabled.
- Durable pre-call purchase records. Duplicate requests return the previous result; uncertain attempts cannot silently spend credits again.
- Exact-email duplicate checks after reveal and again before import. GHL Email ISV v3 must explicitly return a deliverable, low-risk result for the requested address.
- Explicit GHL import with industry/audience tags and optional owner, confirmed only when GHL returns a contact ID. The landing page remains on the stored audience. Import can invoke existing GHL workflows; the builder does not send outreach or enroll contacts in sequences.
- An opt-in scheduler job advances suppression every minute and starts a new sweep after 30 minutes. The scheduler never buys, verifies, imports or sends.
- Login protection, JSON/custom-header write protection, non-cacheable responses, operation attribution and cross-worker serialization in the shared Hub database.

## Configuration and first run

1. Set `APOLLO_API_KEY` with access to People API Search, Contacts Search, Bulk Create Contacts and People Match. Actual access depends on the Apollo account/plan.
2. Reuse the Hub's `GHL_PRIVATE_TOKEN` / `SMART1SUITE_PRIVATE_TOKEN` and `GHL_LEAD_LOCATION_ID`. Use the Smart 1 Marketing **location ID**, not an agency/company ID. The token needs contact read/write access and permission to use billed email verification.
3. Optional `PROSPECT_SUPPRESSION_LOCATION_IDS`: additional comma-separated sub-account IDs; the token must cover every account. The primary account is always included. Maximum 20 accounts. Account discovery and exporting all agency accounts are not automatic.
4. Open the page and choose **Test read access**. This probes existing Apollo search/contact and GHL contact-read endpoints; it does not validate write permissions or billing. Then start background suppression sync and wait for both stages to complete. A queued job advances one page per scheduler tick, even after the browser closes. Use **Refresh progress** to check it. Provider errors pause the job; review the error before choosing Resume. A failed/incomplete source prevents search and purchase.
5. Set `PROSPECT_PAID_ENABLED=1` only after provider access and account billing have been checked. The UI still requires approval of the exact selection and the separate GHL verification charges. No paid calls were made during development.
6. Set `PROSPECT_AUTO_SYNC=1` to enable recurring suppression. Both prospect flags default off. A manually queued one-time sync does not require this flag, but it requires the normal Hub scheduler (`HUB_SCHEDULER`, enabled by default on Render). Normal scheduler leadership and operation locking apply. Paused jobs require explicit resumption even when recurring sync is enabled.

## Workflow improvements

- Connection readiness shows configured accounts, recent read-check results and untested permissions. Checks expire after one hour or a credential/location change. Provider payloads and credentials are not sent to the browser.
- Navigation links and next-action text guide Sync → Search → Review → Import. Search and purchase controls explain the suppression prerequisite. Saved history can be opened independently of a new search.
- Selection review is available while paid operations are disabled. Approval and purchase still enforce the server-side paid gate. The displayed credits are a planning estimate, not an account billing quote; GHL verification volume is shown separately and dollar pricing remains unknown.
- Interrupted purchase/import rows include stage-specific reconciliation guidance. The interface never clears the operation lock or retries an uncertain paid call.
- Background purchasing, automated paid reconciliation and GHL webhooks are not introduced in this change. Purchases still run from the reviewed browser workflow. Live provider validation remains required before enabling them.

## Deliberate limits

- Industry matching uses Apollo keywords, not a guaranteed NAICS/SIC industry filter. Geography targets company headquarters; ZIP-radius filtering is not implemented.
- Apollo hides emails and may obscure names before reveal. Identity suppression reduces duplicate purchases but cannot guarantee zero paid duplicates. Results say **no known duplicate**, not guaranteed net new. Exact-email checks happen after reveal.
- Existing contacts without usable identity data are counted as unmatchable. Additional emails are kept locally for exact matching.
- A saved-contact universe above Apollo's 50,000-record display limit blocks completeness. Search returns 25 rows per page, through page 500; it does not claim a global deduplicated audience count.
- Suppression coverage expires one hour after completion; a location or credential change invalidates it. Concurrent changes made directly in GHL/Apollo can still occur between checks. GHL's configured duplicate-contact behavior also applies to upsert.
- Verification older than 24 hours cannot be imported. Unknown, catch-all, high/medium-risk, mismatched-address or unrecognized response formats are held. No permissive fallback treats a failed verification as success.
- Existing GHL contacts are never updated by this import path when the pre-import lookup finds them. The remaining external race between lookup and upsert cannot be made atomic by SmartHub.
- The initial release uses periodic sync rather than contact-created/DND webhooks, and does not add open/click/visit attribution or an outreach sender.
- API keys are read from the environment; the builder does not put them in browser storage. The shared database contains contact data and must retain the Hub's normal access controls and backups.

## Interrupted operations and recovery

`industry_prospect_records` stores suppression records, saved contacts, audiences, plans, purchases and the `operation-lock`. Paid attempts are persisted **before** the call. A crash between a provider response and saving it is intentionally held for review.

- A normal HTTP/provider failure releases the operation lock. Refresh and inspect the purchase history. Do not reset `review_required`, `reveal_pending`, `verify_pending` or `import_pending` to make a blind retry.
- A process crash can leave `operation-lock`. An administrator should first confirm the original worker has stopped, then reconcile any pending purchase against Apollo credit/history and any pending import against GHL contact records. Only then remove that one lock row through the database administration tooling. Never clear the purchase ledger to retry.
- If a revealed email is already stored, it must not be revealed again. Reconcile the verification or import step independently. Recovery remains an administrative operation in this release; there is no automatic retry button for uncertain billed work.

## Validation

`python test_industry_prospects.py` runs offline against a temporary SQLite database and mocks every network request. It exercises duplicate matching, suppression completeness, approval/freshness gates, cross-worker locking, paid-attempt idempotency, verification, import guards and authentication/CSRF. The test is registered in `.github/workflows/checks.yml`.

## Provider references (checked September 12, 2026)

- [Apollo People API Search](https://docs.apollo.io/reference/people-api-search)
- [Apollo Bulk Create Contacts](https://docs.apollo.io/reference/bulk-create-contacts)
- [Apollo Contacts Search](https://docs.apollo.io/reference/search-for-contacts)
- [Apollo People Enrichment](https://docs.apollo.io/reference/people-enrichment)
- [GHL Contacts Search](https://marketplace.gohighlevel.com/docs/ghl/contacts/search-contacts-advanced/)
- [GHL Duplicate Contact Search](https://marketplace.gohighlevel.com/docs/ghl/contacts/get-duplicate-contact/)
- [GHL Email Verification v3](https://marketplace.gohighlevel.com/docs/ghl/email-isv/verify-email/index.html)

### Local validation results

- 20 prospect-builder Python tests passed.
- The JavaScript workflow test passed form capture, search results, duplicate exclusion, purchase approval and explicit import. `node --check` also passed.
- Existing GHL scope checks: 105 passed. Scheduler health: 34 passed. Menu layout: 237 passed.
- Desktop and narrow-window previews rendered successfully. Browser automation did not confirm button activation; the event flow was verified separately by the JavaScript test above.
- The isolated release checkout passes all 10 CI-coverage checks. Both new prospect-builder test files are registered in the workflow.
- Local full-app checks require a writable `SESSION_FILE_DIR` because Google Finder defaults to a Unix `/tmp` directory. The check uses temporary SQLite; Sites Admin's Postgres-only module therefore runs its existing fallback.
- Live Apollo/GHL API access, verification billing and production deployment have not been exercised. All purchase/import tests used mocked providers.
- Full composed `wsgi.application` smoke check passed: the new page, authenticated status API, JavaScript and stylesheet all returned HTTP 200.
