# YouTube Ads workspace

Entry: Product Success → YouTube Ads, `/tools/youtube-ads/`.

This separate tool uses the existing Smart 1 Ads credentials and shared database.
YouTube Data API access alone does not authorize paid campaigns. Google Ads API
access requires the existing OAuth adwords scope, refresh token, developer token,
and access to the selected advertiser account. The page distinguishes configured
credentials from verified account access. No credentials are returned to the page.

## Workflows

- Build: save account-scoped drafts, review all settings, copy a draft for edits,
  validate with Google, and create a Demand Gen video campaign atomically.
  Campaign and ad are paused. Creation fixes bidding to Maximize conversions,
  applies location/language criteria, and enables only selected YouTube surfaces.
  Staff must review conversions, audience targeting, location presence settings,
  logo eligibility and policy approval in Google Ads before enabling.
  Audience notes are saved planning information, not uploaded targeting.
- Optimize: evidence-based review suggestions for missing delivery, spend without
  conversions and low click-through rate. Changes are reviewed in Google Ads.
  Thresholds are labelled heuristics rather than industry benchmarks.
- Monitor: fresh Video and Demand Gen campaign results for the last 7 or 30 days.
  Optional refresh every five minutes while the page remains open and visible.
  This is not a server scheduler or an email alert service.
- Report: fresh CSV export and printable loaded results, including currency,
  account timezone, window and timestamp. Existing campaign totals may include
  non-YouTube inventory; these are not claimed as YouTube-only results.

Drafts use `youtube_ad_drafts` on the shared SQLite/Postgres engine. Every write
requires Hub authentication and a session CSRF token. A database compare-and-set
claim prevents two workers creating the same draft. An uncertain submission is
locked for manual Google Ads reconciliation; it is never automatically retried.
Edits create a new draft and require new validation. No campaign is enabled here.

## Validation and release

Run `python test_youtube_ads.py` and `node --check hub/static/youtube-ads.js`.
The Python tests also render an offline HTML fixture under `tmp`. Then run
`node test_youtube_ads_ui.cjs` with Playwright available (or set
`PLAYWRIGHT_MODULE` to its installed module path) and Microsoft Edge installed.
The browser test intercepts every request and checks desktop/mobile workflows.
Offline tests cover page rendering, authentication, CSRF, validation, account
boundaries, paused creation, duplicate prevention, uncertain submissions,
report calculations and CSV formula protection. Live Google validation and a
paused creation still need to be exercised against an authorized test account.
No Google credentials or live campaigns are used by the offline tests.

API references:
- https://developers.google.com/google-ads/api/docs/demand-gen/create-campaign
- https://developers.google.com/google-ads/api/docs/demand-gen/channel-controls
- https://developers.google.com/google-ads/api/docs/video/overview
