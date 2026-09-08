# Pickaxe → Smart Hub integration

Nine Pickaxe tools from the "Smart 1 Test" workspace, absorbed into Hub
prompts or wired for a live Pickaxe call. Landed from the Pick Axe package
(reviewed 2026-08); ROI for Digital Products is excluded at the owner's
direction — it collided with proposal_spec's computed-ROI directive, and if
wanted later its home is internal sales prep, never the proposal.

## What is in the repo

| File | What it is |
|---|---|
| `hub/prompts_harvested.py` | The 8 harvested prompts + 1 new one, as data — template, temperature, target module, prefill map, integrator notes |
| `hub/pickaxe.py` | Live-call client, mirroring `hub/ai.py` (retries, usage rows via `quotas.record("pickaxe", …)`, `PickaxeUnavailable` contract, outro stripping) |
| `hub/pickaxe_registry.py` | Field-id maps for the two live-call Pickaxes: SEM Quote Help and Audience Finder |
| `modules/ads_builder/copy_ideas.py` | The first wiring: AD_COPY and AD_EXTENSIONS absorbed, SEM Quote Help live with a Hub-AI fallback |

## Landed (this PR)

**Smart 1 Ads** — `AD_COPY` and `AD_EXTENSIONS` absorbed; SEM Quote Help live
via `hub/pickaxe.py` + the registry; the config block, the `/health` row and
the `pickaxe` quota row. Set `PICKAXE_API_KEY` in Render's environment, never
in the repo. With a workspace-scoped key for "Smart 1 Test",
`PICKAXE_WORKSPACE_ID` is unnecessary; with a Personal key set it to
`50eb9802-678d-4be1-afe1-b615fba85dea`.

**The endpoint carries a VERIFY note.** Pickaxe's own published pages confirm
the base host, Bearer auth, one endpoint per agent, server-side conversation
ids and usage in every response; the exact path spelling and response field
names are transcribed from examples rather than exercised (their docs site is
unreachable from the build sandbox). Every caller falls back to the Hub's own
AI, so a wrong path costs the Pickaxe answer, never the feature — the first
live call with a real key is what confirms it, and the recorded `http_404`
rows on the usage page are what would say it is wrong.

## Landed since

**Scripts** — `RADIO_SCRIPT` and `TV_SCRIPTS` are wired into the Proposal
Builder's creative gate (`/sales/builder/api/draft-spot`): a gated audio or
video line answered "Smart 1 produces it" carries a first-draft spot out of
the same step that recorded the answer, stored on the quote as internal
working notes and never on the client document or the IO. Deliberately not
wired into Fan Radio, Radio Promo or the Commercial Builder — each already
has its own budget-aware writer, and a second writer beside one is the
two-proposal-builders failure.

**Page analyzers** — `CTA_ANALYZER` is `hub/cta_review.py`, one reading for
three screens (the SEO client record's Site Audits card, the Website Audit
tool, the Landing Page Maker's prospect row): the page is fetched and
measured by `modules/ads_builder/landing_page.observe()` first, an
unreadable page is refused as *not measured* rather than reviewed anyway,
and the observation travels beside the review so no screen has to take the
model's word for what is on the page. `SOCIAL_PAGES_REVIEW` and
`CONTENT_CALENDAR` are the Social Planner's `/api/pages-review` and
`/api/calendar-draft` — the review is fed what the last site audit measured
(`scan_facts.social_snapshot`) plus the record's saved profile URLs, with
"no data could be retrieved" said per platform and a client with nothing
measured refused rather than billed for a page of "not reviewed"; the
calendar draft is a brainstorm that creates no slots, because the month
builder is what makes posts.

**Proposal briefing and the Snap** — `SPEND_AND_DEMO` is the Proposal
Builder's `/api/spend-demo`, a *Market briefing (internal)* panel on the
**Budget step** rather than the Executive Summary the roadmap first named:
that step's own question ("what's the working budget?") is the question the
briefing answers, and the summary here is a document section rather than a
step. It is the one harvested prompt whose whole job is the model's general
knowledge, so it is labeled a briefing rather than dressed as a reading,
stored beside the quote as internal notes (`S.marketBriefing`), and reaches
neither the proposal nor the IO — anything a rep carries into a section
passes through `clean_ai_text()` like any other edit. The prompt's own
"Hmm, I am not sure." hallucination brake is kept. `SNAP_CONCEPT` is the
Landing Page Maker's `/api/landing/snap-concept`: the Snap positioning
language lives in that prompt and nowhere else in writing, the draft is an
idea to talk through, and it builds and saves nothing — the Build button is
what makes a page.

**Audience Finder** — the second live-call tool, wired at two grains. Per
campaign: the Proposal Builder's `/api/find-audiences` asks the agency's
audience catalog and tick-gates what comes back into the campaign. Per
client — the "One Audience, Four Readers" build — `hub/audience_spec.py`
holds one confirmed audience per client: the Client 360 **Target audience**
card proposes (a billed button, the same Pickaxe with the Hub's own AI as
the labeled fallback), a rep ticks and keeps, and the confirmation is read
by the Proposal Builder's audience step (offered as one-press adds), the IO
Builder's audiences question (its segments join the options, with a line
saying where they came from) and `AD_COPY`'s `{audience}` prefill
(`for_prompt()` — a value typed on the campaign always wins). Nothing is
written by proposing, a failed read is never "no audience", and clearing is
its own verb. The reply parser and candidate shaping are shared with the
proposal route so the two callers cannot drift.

## Still to wire

Nothing. Every tool harvested from the package is absorbed or wired; the
only excluded one (ROI for Digital Products) is excluded at the owner's
direction, and Overcome Objections still awaits its prompt-frame export
before it can be harvested at all.

Rules that hold for every step: the prompts are near-verbatim from Pickaxes
that produced accepted output for two years — do not rewrite them in the same
PR that moves them; anything whose output can reach a proposal goes through
`proposal_spec.clean_ai_text()`; and each absorb lands in a module being
edited anyway, so that module's env-var reads move onto `hub/config.py` in
the same PR (the opportunistic-migration rule).
