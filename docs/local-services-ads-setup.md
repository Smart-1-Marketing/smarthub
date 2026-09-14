# Smart 1 Ads — Local Services Ads

Open **Smart 1 Ads → Local Services Ads**.

1. **Choose account.** The existing Google Ads connection is checked automatically. Search for a business or choose new business if the account has not been created. Account owners can accept the manager invitation through Google; refresh the connection afterward.
2. **Business details.** Enter the business name, lead phone number, country and postal code. A website is optional.
3. **Services and budget.** Record services, coverage areas, weekly planning budget and answering hours. Saving a plan does not change Google settings.
4. **Finish in Google.** Copy the saved plan, open the appropriate Google setup page, and complete eligibility, Business Profile matching, requested verification, billing and campaign settings. Google does not support creating Local Services campaigns through the Ads API. The wizard does not claim to submit or approve them.
5. **Review and manage.** Return with the account ID and check campaign status, budget amount and period, account spend, lead records and verification history. Use the Google handoff to change campaign settings or review conversations and lead feedback.

**Save and continue** saves the current details and the next step. **Save draft** saves without advancing. Use **Resume a saved setup** after reopening the tool. Drafts are shared within the existing authenticated Smart 1 Ads team workspace. Concurrent edits are detected rather than silently overwritten.

## Existing connection

The existing production Smart 1 Ads connection was verified through the authenticated live interface on September 12, 2026. It loaded 119 enabled client accounts under manager 819-019-5916. American Air - LSA was readable and had a paused Local Services campaign. Existing Render credentials are reused by the same Google Ads client; no new credentials or environment variables are required.

## Reporting boundaries

- Reports cover 7, 30 or 90 complete days in the account time zone.
- Campaign metadata is read separately from performance so campaigns with no traffic remain visible.
- Traditional Local Services and Local Services Performance Max are detected separately.
- Failed reads remain unavailable, with their error visible; they are not presented as zero results.
- Lead rows use creation date. Spend and credits can post later, so the spend-to-charged-lead ratio is descriptive, not an exact invoice reconciliation.
- Local Services Performance Max can have different lead coverage, so the legacy lead-cost ratio is withheld for these accounts.
- Up to 100 recent lead records and 50 verification history records are displayed. Verification history does not establish overall approval.
- This release does not create campaigns, enable ads, change budgets, send messages or connect QuickBooks/RTB.

## Validation

`python test_ads_local_services.py`: seven passing tests covering draft persistence, edit conflicts, validation, connection errors, LSA-only spend, partial reporting failures, Performance Max handling and template rendering.

Browser verification used a temporary local database and a clearly labeled sample account. Saving each setup step and reopening the saved handoff after page reload passed. Production Google reads used the existing authenticated site.

## References

- https://developers.google.com/google-ads/api/docs/campaigns/local-service-campaigns
- https://developers.google.com/google-ads/api/fields/v25/local_services_lead
- https://support.google.com/localservices/answer/6226575
