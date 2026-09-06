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

## Still to wire (in this order)

1. **Page analyzers** — `CTA_ANALYZER` as a shared helper (Landing Page
   Maker, SEO client page, Homepage review); `SOCIAL_PAGES_REVIEW` +
   `CONTENT_CALENDAR` into the social planner. The analyzers need real page
   text: `{page_text}` / `{pages_block}` is the fetched page, and absent data
   is labeled "no data could be retrieved", never invented.
2. **Proposal last** — `SPEND_AND_DEMO` on the Executive Summary step (not
   the Cover — the cover is visual and carries no copy). Touches the document
   clients sign, so it goes after everything else has been read by a person.
   `SNAP_CONCEPT` → Landing Page Maker rides along here or with step 2.
3. **Audience Finder** — spec'd separately ("One Audience, Four Readers"):
   `hub/audience_spec.py`, the rep-confirmation gate, the IO and proposal
   reads, the client page. Its registry entry is already in
   `hub/pickaxe_registry.py` so both live-call tools share one file.

Rules that hold for every step: the prompts are near-verbatim from Pickaxes
that produced accepted output for two years — do not rewrite them in the same
PR that moves them; anything whose output can reach a proposal goes through
`proposal_spec.clean_ai_text()`; and each absorb lands in a module being
edited anyway, so that module's env-var reads move onto `hub/config.py` in
the same PR (the opportunistic-migration rule).
