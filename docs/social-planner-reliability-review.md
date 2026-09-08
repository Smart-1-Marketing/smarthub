# Social media planner: reliability review

Reviewed the merged five-step planner, its staff and client approval paths,
image generation/upload/storage, calendar construction, and CSV export.
Changes below are on `codex/social-planner-reliability`; they have not been merged or deployed.

## Fixed in this review

| Problem | Resulting behavior |
| --- | --- |
| A slow AI response or a stale snapshot could overwrite a newer plan. | Plan writes use the shared atomic JSON update helper and revision checks. Conflicting writes return HTTP 409 instead of replacing newer work. A late save cannot recreate a deleted plan. |
| Editing one post sent every post back to the server. | The browser saves only the selected post and sends the revision it edited. Save failures retain the edit on screen, show a retry action, and prevent navigation from silently discarding it. |
| Saving, drafting, and changing steps could overlap. | Controls are locked while a write is running. Successful drafts return the current plan revision. The browser warns before closing during a write or with an unsaved post. |
| An unfinished plan could be marked approved; changing images or links could retain approval. | Full-plan approval requires copy in every slot and no blocking flags. Changes to approved content or brief details require review again. Client approval is cleared when its content changes. |
| A client with an old review page could approve replacement content. | Approval now includes a fingerprint of the post and its brief. A stale page must reload before approving. Correctness checks also run on the approval endpoint. |
| Successive photo previews reused a storage filename. | Each upload or generated preview has a unique filename. Missing image hosting is detected before a paid image-generation call. |
| Oversized or malformed requests could produce confusing errors. | Uploads have a request-size limit, and malformed JSON/list fields return clear errors. Post and image URLs are checked for valid web addresses without embedded credentials. |
| Publishing CSVs could contain blocked content or past dates. | Publishing exports reject blocking errors, empty exports, and written posts with past dates. The review CSV remains available for unfinished or historical work. |
| Reopening a saved plan lost its holiday selections. | Saved holiday choices and their selected state are restored. |

## Recommended next additions

### 1. Recoverable drafts and edit history

Save setup, selected ideas, and brief fields before the user presses Create plan.
Offer Resume draft and restore an earlier revision. Current save recovery protects
post edits during the open browser session; it is not persistent recovery of the
entire wizard. Also distinguish editing an existing plan's brief from creating a
separate plan so going back through the wizard does not encourage duplicates.

### 2. A calendar that detects real gaps and duplicates

Compare the proposed cadence with other saved, approved, and scheduled plans for
the same client. Flag repeated topics, crowded dates, and excessive reuse of a
photo. The current gap counter measures unassigned dates in the new plan; it does
not reconcile an existing publishing calendar. Let users move or remove a slot
and set the client's timezone without rebuilding their work.

### 3. Channel-specific previews and media checks

Show the final caption, hashtags, link, and media together for each selected
channel. Add crop previews, minimum-resolution checks, aspect-ratio guidance,
alt text, and explicit image/video requirements. The current asset check is
generic and should not be presented as a full platform compatibility guarantee.

### 4. Safe generation retries and cancellation

Persist an image-generation job ID and its finished preview. Retrying delivery
should retrieve that preview rather than charge for a second generation. Add
Cancel remaining posts to bulk drafting and report exactly which posts completed.
The new storage preflight prevents one avoidable charge, but it cannot prevent
every partial failure after an AI provider has already completed a request.

### 5. A deliberate workflow for already delivered posts

Treat edits to scheduled or published content as a new revision requiring an
explicit update/publish action. Clearing local approval does not itself update
content already sent to another system. Add delivery reconciliation and a visible
last-checked status before calling this end-to-end publishing reliable.

### 6. A short readiness checklist

Replace a collection of counts with actionable items: Write 3 posts, Choose 2
required images, Resolve 1 claim, Review the final plan. Each item should open the
next affected post. Keep the five-step flow and optional research tools.

## Validation and limits

- 153 focused planner checks passed, including stale writes, late AI responses,
  approval invalidation, stale client review pages, unique image names, and export guards.
- Image-provider tests use fixtures and mocks; these are not live-provider acceptance tests.
- JavaScript syntax and template checks passed. Browser checks covered photo saving,
  sequential drafting, and controls recovering after requests.
- The broader social-content suite did not finish in this environment. Its placeholder
  image URL fixture was updated for the URL validation rule; no full-suite pass is claimed.
- The shared JSON store supplies cross-worker file locking on the production POSIX
  filesystem. Multi-host storage, lock failures, and record/index recovery still
  need operational testing; revisions are not a substitute for backup restore drills.
- Live Suite delivery, real provider outages, and full channel compatibility remain
  separate acceptance tests. This review strengthens the planner; it does not
  certify the entire publishing pipeline as failure-proof.
