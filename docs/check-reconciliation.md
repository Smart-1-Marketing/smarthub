# Check Reconciliation

Private SmartHub tool at `/tools/check-reconciliation/` for receiving paper checks into QuickBooks Online.

## Required Render environment variables

- `CHECK_RECONCILIATION_ALLOWED_EMAILS` — comma-separated SmartHub account email(s) allowed to use this tool. Keep this to the owner/accounting administrator only.
- `QBO_CLIENT_ID` — Intuit developer app client ID.
- `QBO_CLIENT_SECRET` — Intuit developer app client secret.
- `QBO_REDIRECT_URI` — exact callback registered in the Intuit app, e.g. `https://YOUR-HUB.onrender.com/tools/check-reconciliation/oauth/callback`.
- `CHECK_RECONCILIATION_ENCRYPTION_KEY` — encryption secret for stored OAuth tokens. If absent, the module will use `TOKEN_ENCRYPTION_KEY`.
- `OPENAI_API_KEY` — optional but recommended; used to read payer/date/amount/check number from check images.
- `OPENAI_VISION_MODEL` — optional, defaults to `gpt-4o`.
- `CHECK_RECONCILIATION_DATA_DIR` — optional, defaults to `/var/data/check-reconciliation` on the persistent disk.

## Intuit setup

Create/choose an Intuit Developer app with the QuickBooks Online Accounting scope. Add the exact `QBO_REDIRECT_URI` to the app's Redirect URIs. No Intuit secret is ever sent to the browser; OAuth token exchange and payment creation happen server-side.

## Safety model

1. The normal SmartHub authentication guard must pass.
2. A real SmartHub account cookie is required; the shared panel password is rejected.
3. The account must be Admin/Super Admin and its email must appear in `CHECK_RECONCILIATION_ALLOWED_EMAILS`.
4. Matching a payer does not post money. The user must choose customer(s), review open invoices, allocate the full check, and explicitly confirm the final posting dialog.
5. The server re-reads open invoices immediately before posting and blocks allocations larger than the current balance.
6. The module blocks re-posting a check record and also blocks a duplicate posted check when check number/date/amount repeat.
7. QuickBooks Payment IDs and post-payment invoice balances are stored in the audit record.

## Workflow

Upload a check image. SmartHub extracts the payer/date/amount/check number when OpenAI vision is configured. `Find QuickBooks client` loads active QBO customers and ranks fuzzy name matches. A confirmed match is stored as a payer alias, so `Trasin Corporation` can permanently map to `Trasin Asphalt and Concrete` on future checks.

After customer confirmation, the tool pulls open invoices for the selected customer(s). It first suggests an exact single-invoice match, then a match to invoice principal before a late fee, then exact combinations of up to four invoices. Late-fee matches are flagged instead of silently consuming or erasing the fee.

The approval step can split one check across several invoices and, when the payer maps to multiple QBO customer records, across several customers. The server creates one QuickBooks `Payment` per QBO customer, each linked to the approved Invoice IDs, then re-queries open invoices and stores the resulting balances.
