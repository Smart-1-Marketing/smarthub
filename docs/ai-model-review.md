# AI Model Review

Diagnostics → AI Model Review (`/diagnostics/ai-models`) is an admin-only review
inventory. Opening it makes no provider requests. “Check model availability”
uses the existing linked-environment key for GET /v1/models; it never generates
content. A listing is evidence of visibility, not endpoint compatibility.

The initial recommendations are source-dated, human-selected candidates, not
automated quality scores. Other account-visible models appear as unassessed.
Availability older than seven days becomes unverified. A failed refresh retains
the previous dated results. Review decisions retain the actor, active model,
candidate, reasoning and timestamp. Saving a recommendation does not activate it.

## Shared profiles

| Profile | Optional override | Existing setting retained | Default retained |
|---|---|---|---|
| Commercial writing | COMMERCIAL_OPENAI_MODEL | OPENAI_TEXT_MODEL | gpt-4o-mini |
| Commercial images | COMMERCIAL_IMAGE_MODEL | OPENAI_IMAGE_MODEL | gpt-image-1 |
| Radio Promo writing | RADIO_OPENAI_MODEL | OPENAI_MODEL | gpt-4o |
| Radio Promo images | RADIO_IMAGE_MODEL | OPENAI_IMAGE_MODEL | gpt-image-1 |
| Fan Radio writing | FAN_RADIO_OPENAI_MODEL | OPENAI_MODEL | gpt-4o-mini |

No key, default model or voice configuration was changed. The profiles resolve
at request time, and existing SDK/HTTP transports remain in place. Before using
newer models, test the current request parameters and SDK support; this release
does not certify the candidates as drop-in replacements.

## Review procedure

1. Refresh account availability and read the linked official model documentation,
   pricing and deprecations. Model IDs alone do not establish which is best.
2. Compare the active model and candidate on the same representative briefs,
   including required disclaimers, hard pronunciations, short slots and revisions.
3. Record required-content failures, human quality judgments, actual rendered
   duration, latency, errors and cost, with references to comparison samples.
4. Save keep/test/recommend/reject and the evidence. Review history never changes
   production settings. Model changes remain a separately tested deployment.

## Scope and next stage

### Structured comparison evidence (September 12)

The writing profiles now have a comparison form on the same Diagnostics page.
Use one of three versioned fictional briefs (15-second offer, 30-second
pronunciation, or 60-second revision) with identical prompt/request settings for
the active and candidate models. Supply the two original result references,
spoken scripts, human quality scores, compatibility and listening judgments.
Latency and cost are explicitly reviewer-reported and remain unknown when blank.

Optional PCM WAV uploads are limited to 4 MB each and 180 seconds. The server
checks complete frame data and measures duration; only duration, byte count and
SHA-256 fingerprint are retained, not the audio or base64. Keep the original
samples at their recorded references. Duration measures the complete supplied
file, including silence, and does not verify that its audio matches the script.
Required-phrase checks are literal, case-insensitive and whitespace-normalized;
they do not prove factual accuracy or acceptable pronunciation.

Each submission creates an immutable record through the existing durable JSON
store, including the full brief, rubric version, settings, actor and both samples.
A stale active-model selection is rejected. A candidate with missing/obsolete
wording, excessive duration, failed compatibility/listening, or quality below 3
needs revision. Incomplete measurement evidence yields “More evidence needed.”
A higher human score with complete evidence yields “Candidate leads on this
sample,” not approval to deploy. Cost and speed remain visible tradeoffs and
are not converted into a fabricated overall quality score. Copy the comparison
reference into the final review decision's evidence field.

POST `/api/diagnostics/ai-models/comparisons` is admin-only, JSON-only and rejects
cross-origin requests and request bodies over 12 MB. No provider request occurs
when opening the page or saving a comparison. Existing keys and active model
configuration are unchanged. This stage covers writing and final voice-read
evidence; image fidelity remains a manual review in the existing review form.

The evaluation structure follows [OpenAI evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices):
task-specific examples, explicit criteria and human review. The shipped briefs
are a starting set, not statistical proof of a model-wide improvement.

This release provides the inventory, shared settings, comparison evidence and review history. Automated
paid comparisons, background evaluation jobs, per-asset model/prompt versioning,
one-click activation/rollback and new voice providers are not implemented.
The next stage should add a durable, budget-limited comparison queue with fixed
test briefs and measured audio duration before offering an activation control.

Unknown prices now produce an incomplete total and a known-cost subtotal rather
than silently inheriting GPT-4o Mini pricing. Existing image rates remain rough
estimates, not authoritative billing. The provider dashboard remains authoritative.

Validation: `python -X utf8 test_ai_model_review.py`, plus the existing radio,
commercial, IO and usage regressions. Tests mock provider requests and use temporary
storage; no production credentials or generated assets are needed.
