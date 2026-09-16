## A client's email campaigns, read from their own sub-account

`hub/suite_email_stats.py` reads the sent email campaigns on a client's
Smart 1 Suite sub-account -- names, send dates, delivered/opened/clicked/
bounced/unsubscribed -- once a night and on a Refresh press, and stores
one reading per client per day. Client 360 draws it as the **Email
campaigns** card; the client's report carries it as the **Email
campaigns** section (page, data.json and PDF) through
`modules/reports/suite_email.py`, gated on a live email product and a
linked sub-account. `test_suite_email_stats.py` holds it.

Six things to know before touching it:

1. **It runs on the client's location token, never the Smart 1
   credential.** `suite_accounts.token_for(client)` mints the token from
   the agency install; which sub-account is the client's is the Suite
   Account card's decision and is never restated here. The credential
   `hub/ad_proof_email.py` sends proofs with reads Smart 1's *own*
   account and must not be used for this.
2. **Two scopes, one re-consent.** `emails/schedule.readonly` (the
   campaign list, which carries statistics when asked with
   `showStats=true`) and `emails/stats.readonly` (the per-campaign
   unified statistics endpoint). Both are on `hub/ghl_scopes.py`'s
   requested list. Because the Marketplace app is an agency-level
   install, one agency-owner re-consent grants them for every
   sub-account -- clients do not re-authorize individually. Until then
   `scope_state()` reports them missing, the card says so in words, and
   the nightly sweep does not run at all (one refusal would repeat for
   every client).
3. **The list is the source of truth; the statistics endpoint fills
   gaps.** HighLevel's published spec (apps/emails.json) documents
   `GET /emails/schedule?showStats=true` but not the field names of the
   statistics it returns, and does not yet document the v3 statistics
   endpoint at all. So `_stats_of()` reads a row tolerantly (a
   `stats`/`statistics` object or top-level counts, under several
   spellings), the raw statistics object is stored beside every reading,
   and the per-campaign endpoint is asked only for rows the list carried
   no counts for, under `STATS_CAP`, with a 404 recorded as "not
   offered". **The first live read against Schmidt's Sausage's
   sub-account is where the key mapping gets confirmed** -- compare
   `raw_stats` on the stored reading with the Suite's own Statistics tab
   and fix `_COUNT_KEYS` from what was stored, not by re-pulling.
4. **Five kinds of nothing, drawn apart.** `not_linked`, `no_scope`,
   `no_snapshot`, `unread`, `empty` -- because a zero printed over a 401
   tells a rep the client never emailed anyone.
5. **Whose row it is, checked on every row.** A campaign whose
   `locationId` names another sub-account is dropped and counted; drafts,
   folders, archived and deleted rows are dropped; only `complete`,
   `active` and `resend-scheduled` schedules are campaigns that were sent.
6. **The totals are arithmetic on stored rows.** 30- and 90-day sums
   count only campaigns that came back with counts, and say how many did
   not. Rates are over delivered, or over sent when delivered is absent.

The sweep hour is `SUITE_EMAIL_REFRESH_HOUR`. The job is
`suite_email_snapshot` on the hourly tick.
