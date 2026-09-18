# Smart 1 Hub — long-form guide

Split out of the root `CLAUDE.md`, one file per topic, in the original order.
The root file keeps the architecture, conventions, delivery and merge rules.
Read the files covering a module before editing it. Cross-references that
say "above" or "below" mean earlier or later files in this list.

| File | Topic | Lines |
|---|---|---|
| [`03-traps-every-one-of-these-has-cost-a-working-feature.md`](03-traps-every-one-of-these-has-cost-a-working-feature.md) | Traps — every one of these has cost a working feature | 1476 |
| [`04-a-fallback-secret-in-the-source-is-a-forgeable-token.md`](04-a-fallback-secret-in-the-source-is-a-forgeable-token.md) | A fallback secret in the source is a forgeable token | 3523 |
| [`05-data-sources-and-which-are-stale.md`](05-data-sources-and-which-are-stale.md) | Data sources, and which are stale | 1178 |
| [`06-wiring-four-call-sites-is-not-wiring-the-module.md`](06-wiring-four-call-sites-is-not-wiring-the-module.md) | Wiring four call sites is not wiring the module | 93 |
| [`07-deleting-a-client-destroyed-four-tables-and-recorded-none-of.md`](07-deleting-a-client-destroyed-four-tables-and-recorded-none-of.md) | Deleting a client destroyed four tables and recorded none of it | 59 |
| [`08-two-guards-on-one-client-account-and-both-worked-about-half.md`](08-two-guards-on-one-client-account-and-both-worked-about-half.md) | Two guards on one client account, and both worked about half the time | 49 |
| [`09-one-company-several-client-records.md`](09-one-company-several-client-records.md) | One company, several client records | 53 |
| [`10-whose-client-is-this-and-what-is-outstanding-on-them.md`](10-whose-client-is-this-and-what-is-outstanding-on-them.md) | Whose client is this, and what is outstanding on them | 198 |
| [`11-a-renderer-we-host-and-the-two-things-that-makes-different.md`](11-a-renderer-we-host-and-the-two-things-that-makes-different.md) | A renderer we host, and the two things that makes different | 151 |
| [`12-hf-render-service-exists-now-as-a-second-process-in-this-sam.md`](12-hf-render-service-exists-now-as-a-second-process-in-this-sam.md) | hf-render-service exists now, as a second process in this same container | 162 |
| [`13-a-plan-that-comes-back-as-json-is-a-plan-nobody-reads.md`](13-a-plan-that-comes-back-as-json-is-a-plan-nobody-reads.md) | A plan that comes back as JSON is a plan nobody reads | 508 |
| [`14-opportunistic-migration-read-this-before-editing-any-module.md`](14-opportunistic-migration-read-this-before-editing-any-module.md) | Opportunistic migration — read this before editing any module | 44 |
| [`15-there-is-one-proposal-builder.md`](15-there-is-one-proposal-builder.md) | There is one proposal builder | 1998 |
| [`16-a-clients-llms-txt-is-hosted-here-and-reached-from-their-own.md`](16-a-clients-llms-txt-is-hosted-here-and-reached-from-their-own.md) | A client's llms.txt is hosted here and reached from their own domain | 163 |
| [`17-a-placement-is-judged-by-its-leads-so-the-page-counts-them.md`](17-a-placement-is-judged-by-its-leads-so-the-page-counts-them.md) | A placement is judged by its leads, so the page counts them | 52 |
| [`18-and-the-visitors-own-half-of-that-placement-was-tested-by-no.md`](18-and-the-visitors-own-half-of-that-placement-was-tested-by-no.md) | And the visitor's own half of that placement was tested by nobody | 79 |
| [`19-the-audit-was-already-paid-for-and-four-screens-read-it-diff.md`](19-the-audit-was-already-paid-for-and-four-screens-read-it-diff.md) | The audit was already paid for, and four screens read it differently | 417 |
| [`20-social-posts-are-drafted-here-and-published-in-suite.md`](20-social-posts-are-drafted-here-and-published-in-suite.md) | Social posts are drafted here and published in Suite | 337 |
| [`21-a-blog-post-carries-more-than-a-title-and-none-of-it-was-bei.md`](21-a-blog-post-carries-more-than-a-title-and-none-of-it-was-bei.md) | A blog post carries more than a title, and none of it was being asked for | 63 |
| [`22-a-reviewer-answers-none-and-the-question-still-needs-an-answ.md`](22-a-reviewer-answers-none-and-the-question-still-needs-an-answ.md) | A reviewer answers "none", and the question still needs an answer | 49 |
| [`23-another-agencys-photograph-captioned-as-the-clients-own-prem.md`](23-another-agencys-photograph-captioned-as-the-clients-own-prem.md) | Another agency's photograph, captioned as the client's own premises | 46 |
| [`24-a-featured-image-named-after-a-title-two-posts-share.md`](24-a-featured-image-named-after-a-title-two-posts-share.md) | A featured image named after a title two posts share | 72 |
| [`25-a-number-a-stranger-controls-and-the-sweep-that-did-not-fini.md`](25-a-number-a-stranger-controls-and-the-sweep-that-did-not-fini.md) | A number a stranger controls, and the sweep that did not finish | 58 |
| [`26-a-comparison-keyed-on-a-string-google-does-not-send.md`](26-a-comparison-keyed-on-a-string-google-does-not-send.md) | A comparison keyed on a string Google does not send | 46 |
| [`27-a-clients-document-published-to-the-agencys-own-blog.md`](27-a-clients-document-published-to-the-agencys-own-blog.md) | A client's document, published to the agency's own blog | 50 |
| [`28-publishing-is-a-prompt-not-a-panel-and-not-a-button.md`](28-publishing-is-a-prompt-not-a-panel-and-not-a-button.md) | Publishing is a prompt, not a panel and not a button | 61 |
| [`28a-except-that-one-of-the-two-cmses-has-had-a-write-api-all-a.md`](28a-except-that-one-of-the-two-cmses-has-had-a-write-api-all-a.md) | Except that one of the two CMSes has had a write API all along | 158 |
| [`28b-schema-needs-one-file-on-the-site-and-that-is-all-it-need.md`](28b-schema-needs-one-file-on-the-site-and-that-is-all-it-need.md) | Schema needs one file on the site, and that is all it needs | 260 |
| [`29-alt-text-is-read-from-the-site-not-invented-for-it.md`](29-alt-text-is-read-from-the-site-not-invented-for-it.md) | Alt text is read from the site, not invented for it | 34 |
| [`30-getting-a-file-back-out-is-storages-job-not-each-modules.md`](30-getting-a-file-back-out-is-storages-job-not-each-modules.md) | Getting a file back out is storage's job, not each module's | 133 |
| [`31-a-cache-that-is-careful-with-credits-on-one-worker-in-two.md`](31-a-cache-that-is-careful-with-credits-on-one-worker-in-two.md) | A cache that is careful with credits, on one worker in two | 56 |
| [`32-a-gpt-ad-is-five-deliverables-and-four-of-them-used-to-arriv.md`](32-a-gpt-ad-is-five-deliverables-and-four-of-them-used-to-arriv.md) | A GPT ad is five deliverables, and four of them used to arrive separately | 65 |
| [`33-two-fields-said-the-campaign-was-blocked-and-nothing-read-ei.md`](33-two-fields-said-the-campaign-was-blocked-and-nothing-read-ei.md) | Two fields said the campaign was blocked and nothing read either | 67 |
| [`34-a-stale-list-that-can-only-be-read-is-a-list-nobody-works.md`](34-a-stale-list-that-can-only-be-read-is-a-list-nobody-works.md) | A stale list that can only be read is a list nobody works | 142 |
| [`35-a-report-that-has-been-opened-has-already-been-run.md`](35-a-report-that-has-been-opened-has-already-been-run.md) | A report that has been opened has already been run | 149 |
| [`36-an-ad-copy-request-is-fourteen-fields-and-the-form-asked-fou.md`](36-an-ad-copy-request-is-fourteen-fields-and-the-form-asked-fou.md) | An ad copy request is fourteen fields, and the form asked four | 73 |
| [`37-a-dropdown-that-cannot-hold-the-answer-is-worse-than-a-text.md`](37-a-dropdown-that-cannot-hold-the-answer-is-worse-than-a-text.md) | A dropdown that cannot hold the answer is worse than a text box | 39 |
| [`38-a-web-ticket-is-eight-fields-and-the-form-asks-for-all-eight.md`](38-a-web-ticket-is-eight-fields-and-the-form-asks-for-all-eight.md) | A web ticket is eight fields, and the form asks for all eight | 75 |
| [`39-a-campaign-support-request-is-twenty-three-fields-and-we-sen.md`](39-a-campaign-support-request-is-twenty-three-fields-and-we-sen.md) | A campaign support request is twenty-three fields, and we sent four | 61 |
| [`40-a-clients-photos-are-already-somewhere-and-it-is-not-their-l.md`](40-a-clients-photos-are-already-somewhere-and-it-is-not-their-l.md) | A client's photos are already somewhere, and it is not their laptop | 159 |
| [`41-one-design-the-whole-size-set-and-the-fourth-copy-it-refused.md`](41-one-design-the-whole-size-set-and-the-fourth-copy-it-refused.md) | One design, the whole size set — and the fourth copy it refused to be | 139 |
| [`42-the-brandtemplate-decision-resolved-a-pick-not-a-table.md`](42-the-brandtemplate-decision-resolved-a-pick-not-a-table.md) | The BrandTemplate decision, resolved: a pick, not a table | 81 |
| [`43-the-one-module-that-is-not-python.md`](43-the-one-module-that-is-not-python.md) | The one module that is not Python | 962 |
| [`44-everyone-has-their-own-login-and-there-are-two-levels-of-it.md`](44-everyone-has-their-own-login-and-there-are-two-levels-of-it.md) | Everyone has their own login, and there are two levels of it | 617 |
| [`45-three-index-pages-and-the-question-each-one-answers.md`](45-three-index-pages-and-the-question-each-one-answers.md) | Three index pages, and the question each one answers | 85 |
| [`46-one-description-of-what-a-record-page-looks-like.md`](46-one-description-of-what-a-record-page-looks-like.md) | One description of what a record page looks like | 221 |
| [`47-declared-and-never-wired.md`](47-declared-and-never-wired.md) | Declared and never wired | 96 |
| [`48-a-review-nobody-wrote-down-is-a-review-nobody-can-point-at.md`](48-a-review-nobody-wrote-down-is-a-review-nobody-can-point-at.md) | A review nobody wrote down is a review nobody can point at | 102 |
| [`49-what-the-ad-report-syncs-propose-and-the-person-who-stands-b.md`](49-what-the-ad-report-syncs-propose-and-the-person-who-stands-b.md) | What the ad-report syncs propose, and the person who stands behind each | 394 |
| [`50-a-clients-google-listing-read-live-rather-than-remembered-fr.md`](50-a-clients-google-listing-read-live-rather-than-remembered-fr.md) | A client's Google listing, read live rather than remembered from the scan | 87 |
| [`51-a-clients-youtube-channel-read-live-on-the-same-key.md`](51-a-clients-youtube-channel-read-live-on-the-same-key.md) | A client's YouTube channel, read live, on the same key | 80 |
| [`52-microsoft-advertising-one-consent-and-the-reports-module-pul.md`](52-microsoft-advertising-one-consent-and-the-reports-module-pul.md) | Microsoft Advertising: one consent, and the reports module pulls | 191 |
| [`53-groundtruth-the-key-arrived-before-the-document.md`](53-groundtruth-the-key-arrived-before-the-document.md) | GroundTruth: the key arrived before the document | 84 |
| [`54-one-industry-taxonomy-resolved-and-written-down.md`](54-one-industry-taxonomy-resolved-and-written-down.md) | One industry taxonomy, resolved and written down | 33 |
| [`56-verifying-a-change.md`](56-verifying-a-change.md) | Verifying a change | 1142 |
| [`57-smartforecast-moved-to-the-hub-database.md`](57-smartforecast-moved-to-the-hub-database.md) | SmartForecast moved to the Hub database, and what the move found | 133 |
| [`58-the-check-that-reported-no-json-on-the-disk.md`](58-the-check-that-reported-no-json-on-the-disk.md) | The check that reported no JSON on the disk | 181 |
| [`59-leads-in-a-table-not-a-file-on-one-instance.md`](59-leads-in-a-table-not-a-file-on-one-instance.md) | Leads in a table, not a file on one instance | 136 |
| [`60-a-clients-email-campaigns-read-from-their-own-sub-account.md`](60-a-clients-email-campaigns-read-from-their-own-sub-account.md) | A client's email campaigns, read from their own sub-account | 65 |
| [`61-amazon-dsp-five-claims-and-four-of-them-fail-as-a-working.md`](61-amazon-dsp-five-claims-and-four-of-them-fail-as-a-working.md) | Amazon DSP: five claims, and four of them fail as a working configuration | 182 |
| [`62-a-scanned-business-that-is-not-a-client-is-a-lead.md`](62-a-scanned-business-that-is-not-a-client-is-a-lead.md) | A scanned business that is not a client is a lead | 119 |
| [`63-the-google-tokens-off-their-own-sqlite-file.md`](63-the-google-tokens-off-their-own-sqlite-file.md) | The Google tokens, off their own SQLite file | 120 |
| [`64-ask-smarthub-reads-the-fact-table.md`](64-ask-smarthub-reads-the-fact-table.md) | Ask SmartHub reads the fact table: named periods, decided flags, recipes | 174 |
| [`65-the-delivery-lock-that-never-spanned-two-instances.md`](65-the-delivery-lock-that-never-spanned-two-instances.md) | The delivery lock that never spanned two instances | 107 |
| [`66-the-stores-that-were-not-json.md`](66-the-stores-that-were-not-json.md) | The stores that were not JSON | 135 |
| [`67-the-client-passwords-in-the-backup.md`](67-the-client-passwords-in-the-backup.md) | The client passwords in the backup | 316 |
| [`68-the-upload-url-that-nothing-ever-served.md`](68-the-upload-url-that-nothing-ever-served.md) | The upload URL that nothing ever served | 110 |
| [`69-the-third-question-bytes-on-the-disk.md`](69-the-third-question-bytes-on-the-disk.md) | The third question: bytes on the disk | 120 |
| [`70-client-360s-header-said-the-same-thing-twice.md`](70-client-360s-header-said-the-same-thing-twice.md) | Client 360's header said the same thing twice, and nothing about who to call | 124 |
| [`71-a-clients-asset-home-has-five-folders-and-the-uploads-get-a-web-ready-copy.md`](71-a-clients-asset-home-has-five-folders-and-the-uploads-get-a-web-ready-copy.md) | A client's asset home has five folders, and the uploads get a web-ready copy | 162 |
| [`72-nothing-built-the-image-that-deploys.md`](72-nothing-built-the-image-that-deploys.md) | Nothing built the image that deploys | 103 |
| [`73-one-page-per-provider-and-the-fields-left-on-the-table.md`](73-one-page-per-provider-and-the-fields-left-on-the-table.md) | One page per provider, and the fields left on the table | 88 |
| [`74-every-link-that-works-without-a-hub-login.md`](74-every-link-that-works-without-a-hub-login.md) | Every link that works without a Hub login | 153 |
| [`75-removal-day-is-readable.md`](75-removal-day-is-readable.md) | Removal day is readable | 125 |
| [`76-the-optimizers-bytes-cross-the-instance-boundary.md`](76-the-optimizers-bytes-cross-the-instance-boundary.md) | The optimizer's bytes cross the instance boundary | 134 |
| [`77-a-half-hour-job-on-the-shared-thread-stalled-every-job-behind-it.md`](77-a-half-hour-job-on-the-shared-thread-stalled-every-job-behind-it.md) | A half-hour job on the shared thread stalled every job behind it | 64 |
| [`78-last-month-was-not-in-the-fact-table.md`](78-last-month-was-not-in-the-fact-table.md) | Last month was not in the fact table | 72 |
| [`79-callrail-a-phone-call-is-an-outcome-not-a-conversion.md`](79-callrail-a-phone-call-is-an-outcome-not-a-conversion.md) | CallRail: a phone call is an outcome, not a conversion | 111 |
| [`80-the-clients-360-script-in-files-not-a-template.md`](80-the-clients-360-script-in-files-not-a-template.md) | The Client 360 script, in files, not a template | 53 |
| [`81-a-copy-of-the-landed-rows-and-two-indexes-the-live-table-lacked.md`](81-a-copy-of-the-landed-rows-and-two-indexes-the-live-table-lacked.md) | A copy of the landed rows, and two indexes the live table lacked | 115 |
| [`82-camhub-a-conditions-page-with-a-sponsor-system-behind-it.md`](82-camhub-a-conditions-page-with-a-sponsor-system-behind-it.md) | CamHub: a conditions page with a sponsor system behind it | 193 |
| [`83-display-ad-builder-handoff-the-next-rounds.md`](83-display-ad-builder-handoff-the-next-rounds.md) | Display Ad Builder: handoff for the next rounds | 249 |
