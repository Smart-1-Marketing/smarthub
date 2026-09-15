# Help Center maintenance — September 15, 2026

Reviewed main through 715103640887df8d889f93d131f0e89c829acc18, following maintenance PR #576 (6121d76c57b24b86a4801e37d896af2e840af638).

Added two contextual LSA articles for setup/reporting and client intake, based on the current LSA workspace template, intake implementation and docs/lsa-ads-workspace.md. Updated five existing articles for gallery duplicate choices, landing-page readiness/conversion/history, reporting refresh/retries, IO delivery recovery, and Client 360 pipeline context.

Existing merged help already covers YouTube Studio and paid ads, Places/channel readings, proposal automatic plans/client answers/progress, and Client 360 landing-page metrics. Preserve those additions. Other merged fixes do not require changed help instructions. The Learning Library source and its 18 recorded lesson IDs are unchanged; no videos or external links invented.

Evidence: hub/report_schedule.py; modules/image_picker/templates/_upload_panel.html; hub/templates/landing_maker.html; docs/io-completion-recovery.md; hub/templates/client360.html; modules/ads_builder/templates/lsa_workspace.html; modules/lsa_ads/intake.py.

No changes to sidebar placement, account controls, notifications, question logging, support form, or advertising behavior. Run Help Center and placement checks plus retrieval checks, merge only on green, then verify served articles and record the successful checkpoint.
