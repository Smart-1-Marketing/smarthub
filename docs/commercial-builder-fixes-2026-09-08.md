# Commercial Builder fixes

Implemented locally after the Commercial Builder review. No deployment, model selection change, or paid provider generation was performed.

## Changes

- HeyGen casting now offers its own public/private voice catalog, voice previews, avatar previews when available, explicit selection, and a separate Generate button. ElevenLabs voice IDs are rejected at the HeyGen boundary.
- Presenter requests reserve a scene before submission and reuse an existing take or block a duplicate pending request. Rejected retakes retain prior media. Ambiguous submission timeouts retain an unknown state so retries do not automatically spend again.
- Temporary HeyGen and compositor polling failures remain recoverable. Presenter polling has backoff and a resume action. Old-job responses cannot overwrite newer jobs, and edits made during storage remain marked stale.
- Successful audio is uploaded and represented by URL metadata rather than raw bytes in JSON. Storage failure reports an error. Full narration includes spoken CTA copy.
- Presenter scenes use HeyGen speech. Mixed spots generate narration only for other scenes and place it at each scene's start. Background footage is explicitly muted.
- Speech and timeline signatures invalidate outdated media. Missing assets, stale narration, pending media, failed presenter storage, and measured presenter-duration mismatches block export even when creative warnings are overridden.
- Failed presenter storage can be retried without generating a new clip. The UI shows that warning for either presenter layout.
- End cards use business identity instead of internal project titles. Malformed render-format requests receive a validation error.
- The existing shared model profile is preserved. Chat-completion options now support Astra/reasoning models without sending unsupported temperature or legacy token-limit options. This is adapter compatibility, not an account/model upgrade or a latency benchmark.

## Recovery operation

`flask --app wsgi:hub_app commercial-recover-presenters --limit 50` polls saved provider job IDs and retries storing completed clips. It never starts a new generation. The command is installed with the feature; no recurring scheduler or verified webhook was deployed. Unknown submissions without a provider ID still require reconciliation with HeyGen before recovery.

## Verification

- 21 focused regression tests passed with isolated SQLite data and simulated providers, including JSON-safe audio, CTA narration, duplicate prevention, stale response/edit races, storage retries, render status recovery, and Astra request options.
- Existing HeyGen suite: 97 passed, 0 failed. Existing wizard suite: 308 passed, 0 failed. Approval-flow fixtures now supply valid media instead of relying on a QC override to render empty scenes.
- Additional mock, client-review, compliance, library, and explainer suites exceeded their 150-second limits during application startup. Their partial results are not counted as passes. Startup was intermittently slow on this Windows checkout; the successful HeyGen/wizard runs followed earlier timeouts.
- Python compilation, edited JavaScript syntax checks, and scoped diff whitespace checks passed.
- Browser validation used an isolated demo application: avatar selection alone kept Generate disabled; selecting a HeyGen voice enabled it; submission explicitly reported that demo mode produced no video.
- The regression suite is included in CI. Local test logs are under `.tools/commercial-review/`.

Live provider acceptance is still required for lip-sync, pronunciation, signed-media ingestion, measured final timing, and 16:9/9:16 output quality. No phone-viewport or full keyboard-only acceptance test was completed. The original review remains a historical record, not a description of the fixed code.
