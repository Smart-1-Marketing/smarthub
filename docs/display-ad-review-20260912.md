# Display Ad creation, review and client approval

Start in **Client 360 → Create display ads**. The brief carries the client and website into the campaign, reuses saved brand choices and verified contact details, and asks for the campaign goal, offer, destination and purchased platforms. Client details and advanced design controls stay collapsed until needed.

Follow **Brief → Design → Review → Send**. The editor autosaves after a pause and shows Saving, Saved or a retry message. A device backup offers to restore an unfinished draft. Conflicting saves preserve the draft and let staff reconcile competing edits.

Review builds a contact sheet of every supported size, platform and concept. Problem sizes appear first, with direct links to their headline, crop and replacement controls. **Approve all passing sizes** signs only sizes whose purchased platforms all pass QA. Warnings and manual replacements require individual visual approval. Send becomes available only after every placement in the proof has staff approval.

Printing always includes the whole contact sheet, even when the screen is filtered to problem sizes. You can also compare saved versions. Comparison uses captured artwork, including older source images; it never recreates historical ads from current assets. Amazon 250×250 remains disabled pending a verified specification.

## Smart 1 emails its client

1. Link and verify the recipient once in **Client 360 → Email client & history**. The contact must belong to Smart 1’s GHL account.
2. Open **Send** from the completed ad review. Check the recipient, enter the sender configured in Smart 1’s GHL account, and edit the subject and message.
3. Choose **Preview email**. Check the displayed addresses, text and exact proof link. Nothing has been sent yet.
4. Choose **Send proof through GHL**. An accepted response means GHL queued the email; actual delivery and replies appear in the linked client’s email history.
5. The client opens the proof without signing in, approves the complete set or requests changes for the set or a particular size. Changes unlock the relevant artwork for staff editing. The old proof stays preserved.
6. Approval produces a download containing the exact approved files, grouped by platform. The campaign timeline records the decision and final package.

The optional `GHL_PROOF_EMAIL_FROM` environment setting pre-fills the sender. Existing Smart 1 GHL token/location settings are reused; contact and conversation read permissions plus message-send permission are required. Recipient changes require re-verification. This flow sends from Smart 1 to its client, independently of the client’s own GHL subaccount.

## Reliability and operations

Proof links contain an unguessable token and expose only that frozen version. Staff sign-offs bind the campaign revision, source assets and exact output bytes. Changed or unapproved artwork cannot be silently substituted during client approval.

Email attempts are saved durably before calling GHL. Double clicks and repeated requests do not send twice. A timeout or uncertain response stays blocked for review in GHL; it is never automatically resent. Reopening Send displays an existing attempt and reconciles a stored receipt with the campaign timeline.

Campaign records and their index use atomic replacement so an interrupted save preserves the previous complete record. Render and review jobs survive a service restart on the persistent disk. Interrupted final packaging is recovered at startup; replaying approval does not duplicate delivery receipts. This queue supports the current single-instance deployment. Scaling to multiple instances requires a shared queue and shared transactional storage.

## Validation

The full Display Ad regression suite passed 413 tests before the final focused additions. Follow-up tests cover overlapping autosaves, failed-save retries, client revision unlocks, receipt reconciliation, email preview/send behavior and reopened send attempts. The HTTP integration test renders a fictional campaign’s 11 sizes, signs them off, approves the public proof and checks the exact final ZIP contents. All GHL sends in tests are mocked; no real client email was sent.

Live deployment verification is recorded separately. No successful live paid AI generation is claimed. Team alert transport and external embedding domains remain configuration decisions separate from this client proof workflow.

Sources: [GHL send message](https://marketplace.gohighlevel.com/docs/ghl/conversations/send-a-new-message/), [GHL message history](https://marketplace.gohighlevel.com/docs/ghl/conversations/get-messages/).
