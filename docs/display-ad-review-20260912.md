# Display Ad review and client email

The editor's **Contact sheet & versions** button saves the current draft and opens a review of every supported size, platform and concept. The sheet can be printed to PDF. Version comparison shows changed fields and the artwork captured for each saved version, where available; it does not recreate historical images from today's assets.

**Approve all passing sizes** signs only sizes whose bought platforms all pass QA. Warnings, failures and manual replacements stay for individual review. Approval checks the saved revision, source assets and exact rendered file. A later change invalidates the operation instead of approving different artwork.

Draft ZIPs are marked DRAFT. Final packages require per-placement approval and list withheld or missing placements. A partial package does not mark the project complete. Replacement previews display the uploaded file itself. Save conflicts offer a draft download and a comparison that combines non-conflicting edits while preserving a choice for conflicting fields.

Abandoned approval renders expire after the cache retention period. Referenced approval artifacts remain protected; corrupt project records prevent that cleanup. Saved versions and completed contact sheets remain available for comparison. Amazon's built-in 250x250 placement is disabled pending a verified specification.

## Smart 1 emails its client

1. Open the client in Client 360 and select **Email client & history**. The Display Ad editor also links to this page.
2. Find the recipient in Smart 1 Marketing's GHL account. Check the displayed name, email and company, then link that contact to the client.
3. Select **Open contact in GHL to email**. In the contact's conversation, choose Email, check the sender and recipient, add the subject and proof URL, and send.
4. Return to Client 360 and refresh email history. The Hub reads the conversation and email messages for that exact contact. Pending, sent, delivered and failed states remain distinct.

This uses Smart 1's existing GHL token and location configuration, not the client's own subaccount mapping. Contact, conversation and message read permissions are required. The Hub never sends an email from the linking or refresh buttons. Unlinking removes only the Hub association and keeps GHL's emails. Changed email addresses or a changed Smart 1 location require re-verification. No real client contact has been linked or emailed during development.

GHL remains the source of the messages and their IDs. The Hub persists the explicitly selected client/contact/location association through its durable JSON store. Recent email views are limited to the latest 20 messages returned by GHL. Live account access and a real recipient still need verification.

## Validation and remaining configuration

- Full Display Ad suite: 395 passed. Expanded HTTP workflow also passed for replacement bytes, full contact-sheet capture, warned-size exclusion, history comparison, approval locks and incomplete delivery.
- Client 360 layout checks: 60 passed. Dedicated offline email tests cover account isolation, exact identity, stale changes, email changes, cross-contact messages, login and origin protection. Browser testing used disposable campaigns and mock GHL contacts.
- OpenAI connectivity, authentication and generation are separate checks. Diagnostics offers a synthetic paid copy test, but no live paid generation result is claimed by this release.
- Team alert recipient/webhook and allowed external embedding domains remain unset by this work. The client-email flow is not an automatic alert transport.

Sources: [GHL conversation search](https://marketplace.gohighlevel.com/docs/ghl/conversations/search-conversation/), [GHL message history](https://marketplace.gohighlevel.com/docs/ghl/conversations/get-messages/), [Amazon display specifications](https://advertising.amazon.com/resources/ad-specs/dsp/desktop).
