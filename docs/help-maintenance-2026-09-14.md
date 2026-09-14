# Help Center maintenance — September 14, 2026

Reviewed first-parent merges after 4a184e2 through cf5d6cc. Recent changes already expanded help for Reports, Proposal Execution plans/owners/kickoff/client links, 360 Skills, Sites Builder and production controls, and revised background-removal walkthroughs. Preserve those additions.

Refresh four existing articles: the weather industry selector now has twelve industries; Google cleanup now distinguishes Update scan from Full rescan and persists resource results; built landing pages provide Copy link and honest form errors; Proposal Execution reopens a proposal version and explicitly supersedes a different open run. Update the existing Google cleanup walkthrough to match the renamed controls.

Evidence: hub/weather_triggers.py VERTICAL_LABELS; modules/google_access/templates/qa_inactive.html and qa_inactive.py; hub/templates/landing_maker.html, hub/landing_render.py and hub/landing_maker.py; hub/templates/proposal_execution.html and proposal_execution_routes.py. Existing Help Center/source registrations are retained, so contextual help and AI retrieval receive the same updated wording.

The Learning Library changed its partner navigation paths. Comparing its video IDs with the last verified commit confirms the same 18 videos; no new or invented tutorial links were added. Account/logout, notifications, Help placement, question logging and the support form are untouched.

The local incremental checkpoint is advanced only after the merged deployment is verified live.
