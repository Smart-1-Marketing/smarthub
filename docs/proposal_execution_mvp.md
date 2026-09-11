# Proposal Execution Center — MVP

## Purpose

Proposal Execution Center turns an uploaded client proposal into one persistent execution graph rather than a list of separate SmartHub tools a user has to visit manually.

The first acceptance case is the updated Monogram Homes 2026/2027 marketing proposal.

## Entry point

`/proposal-execution`

Choose a client, choose an uploaded PDF/DOCX proposal already attached to Client 360, and press **Analyze Proposal**.

## What the MVP does

1. Reads the stored proposal through the Hub's existing safe proposal reader.
2. Extracts channels, budgets and campaign facts with OpenAI when available, plus a deterministic cross-check so obvious proposal lines are not lost if AI is unavailable.
3. Pulls the existing merged Client 360 context and mapped Google-account context.
4. Creates one durable run with dependency-aware tasks.
5. Shows one shared missing-input form. A value such as the landing URL is entered once and fans out to every task that requires it.
6. Lets the user start, pause and retry the batch.
7. Advances background-capable tasks on the Hub's existing single-leader scheduler.
8. Sends Stadium audio script work through the real Radio Scripts tool adapter.
9. Builds internal working briefs for other supported branches while adapters are added incrementally.
10. Generates explicit launch/handoff packets for external activation rather than falsely marking a campaign complete.
11. Stops anything that would publish, schedule, change a live campaign or start spend at an approval/handoff gate.
12. Surfaces Needs Input, Running, Needs Approval, Failed, Completed and Live status in one workspace.
13. Uses the existing Hub background-job notification layer for approvals and failures.

## Monogram task branches

The current graph recognizes Website Retargeting, Paid Search, SEO + AI, Stadium to Screen, Meta, YouTube advertising, monthly social content, monthly YouTube sales video, YouTube channel optimization and ChatGPT/AI advertising.

The Stadium branch creates a media spec, Radio Scripts job, 300x250 companion-banner brief and Venue Replay/media activation handoff. Paid Search, Meta, YouTube, Retargeting, Social and SEO each have their own planning/creative/activation dependencies. Tracking and reporting are shared branches instead of being repeated inside every channel.

## Task states

`draft`, `needs_input`, `ready`, `queued`, `running`, `blocked`, `needs_approval`, `changes_requested`, `approved`, `scheduled`, `live`, `completed`, `failed`, `cancelled`.

## Execution modes

- `auto` — internal research/draft work may finish without another gate.
- `approval` — SmartHub may generate the artifact in the background, but a person must approve it before dependent work continues.
- `handoff` — SmartHub prepares the complete launch packet; the external platform action remains a human step and can only be marked live/completed after approval.

## Scheduler integration

The MVP uses the same once-per-minute, single-leader scheduler already driving `hub.creative_jobs`. Registration installs a small bridge so each scheduler tick may advance one creative job and one proposal-execution task. The proposal queue keeps its own persisted states, retries and stale-running recovery, so leaving the browser does not stop the work.

A later cleanup can give Proposal Execution its own named `hub.scheduler.JOBS` entry without changing the queue or task model.

## Safety rules

- Proposal text is read only from the uploaded proposal record; a caller cannot supply an arbitrary URL.
- No offer, URL, budget, targeting fact or performance claim is invented.
- External activation is never reported as complete merely because a brief or creative artifact exists.
- Launch/spend/scheduling stays behind approval.
- Failed background tasks retry up to three times and then surface as `failed` with the error preserved.
- A stalled `running` task is reclaimed after ten minutes.

## Current adapter registry

- `brief` — generates an internal structured deliverable using OpenAI when available, with a deterministic template fallback.
- `radio_scripts` — creates a persisted set in the existing Radio Scripts tool.
- `launch_packet` — compiles upstream results and campaign inputs into a human activation packet; it performs no external publish/spend action.

This registry is the extension seam. New SmartHub tools should join by registering an adapter rather than adding channel-specific execution logic to the orchestrator.

## Next adapter wave

After the MVP workflow is verified with Monogram, wire direct adapters for Paid Search campaign construction, Meta creative/campaign prep, Social Planner generation/scheduling, SEO task creation/publishing, Creative Studio/display assets, YouTube/video production and reporting setup. The existing handoff packets remain the safe fallback until each adapter can prove the external action it performed.
