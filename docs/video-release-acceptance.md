# Video release acceptance

Before merging, run the Commercial Builder finishing and workflow tests and the existing render, compliance, and client-review suites. The checks workflow runs the new boundary tests with synthetic providers; it must be green before release.

After deployment:
1. Confirm the live version and health endpoint, then inspect application errors.
2. Open the internal test project. Confirm warnings precede the collapsed passing checks, fix links open the right step, and the spending ceiling is visible beside Render.
3. Reconcile earlier provider charges before enabling a paid retest under an existing project limit. Configure CREATOMATE_MAX_USD_PER_CREDIT with a verified upper bound for the account's credit cost. This value is a ceiling, not an invoice rate inferred by the app. Missing prior charges or pricing blocks paid work. Other unpriced generation is blocked for capped projects.
4. Submit one corrected cut using saved footage and narration. Do not repeat a submission while its outcome is uncertain. Failed/uncertain requests retain their budget reservation; reconcile them before any further spend.
5. Inspect the actual output: resolution, duration, decoding, audio presence, black/quiet sections. Watch the full cut for the end card, pronunciation, audio quality and timing. Record the job ID and measured results. A healthy deployment is not a passed video acceptance test.
6. Confirm outdated/unverified/failed cuts cannot be approved. Passing technical checks still require human review; flagged black/quiet intervals require an explicit review acknowledgment.
7. Confirm Create review link does not send, even with an email entered. Test Send for review only with a deliberately authorized test recipient. Do not send an internal QA cut to a client.

Budget accounting retains a conservative reserved maximum for each submitted Creatomate render. It never presents reservations or an incomplete known subtotal as actual total spending. Existing projects have no limit until one is explicitly saved. Earlier spending is unknown until a verified opening ceiling is recorded. Budget changes and reservations are stored in additive tables; no destructive migration is needed.

Creatomate credit calculation reference: https://creatomate.com/docs/account/how-are-credits-calculated (resolution × explicit frame rate × duration, divided by 100 million and rounded up, minimum one credit).
