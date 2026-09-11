# QA item: landing page generator doctrine (George Roberts)

**Raised by:** George Roberts, web development team
**Raised via:** "AI Landing Page Prompt" PDF, forwarded by Todd (CEO) 2026-09-11
**Status:** Completed — merged in [PR #473](https://github.com/Smart-1-Marketing/smarthub/pull/473)

This is a repo record of the request and its resolution. It is not an entry
in the Hub's own `/qa-tasks` tool — this session has no login to the live
Hub at smart1.agency, so it could not be filed there. If this needs to be
tracked as a live QA task as well, file one at `/qa-tasks`, target "Landing
Pages", note it as raised by George Roberts and already resolved by the PR
linked above.

## The ask

George's spec was a system prompt for a direct-response landing page
generator built for raw ad-copy inputs (headline/body/CTA, destination URL,
logo, hex brand colors, phone number, hero image, optional third-party form
embed, optional Google reviews) with a fixed ten-section page architecture,
a static-site zip export, and mandatory GA4 tracking. See the source PDF
(shared in chat, not committed to this repo) for the full spec.

## What was actually done

The Hub already has a Landing Page Maker (`hub/landing_maker.py` +
`hub/landing_render.py`, at `/sales/landing`) that builds pages from real
proposals rather than raw ad copy, and posts leads through `hub/leads.py`
rather than a third-party form embed — both deliberate, already-documented
decisions elsewhere in this Hub (see `CLAUDE.md`). Rather than build a
second, parallel tool matching the PDF's raw-input/zip-export shape, George's
copywriting and honesty doctrine was folded into the existing tool:

- The AI `SYSTEM` prompt now states the doctrine directly: message match,
  pain-then-relief-then-proof, specificity over cleverness, real urgency
  only, and proof ordered to answer the biggest doubt first.
- A rep can paste in 1–2 real Google reviews (`Name | rating | quote`);
  with none given, the social-proof section is omitted rather than showing
  a placeholder to a prospect, and the build response flags the gap.
- A client's real GA4 measurement ID, if supplied, wires phone-click and
  form-submit event tracking; an invalid or missing one is dropped rather
  than guessed at.
- Accessibility: visible focus states on buttons/links, more meaningful
  logo alt text.

Not adopted: the raw-input/zip-export/third-party-embed mechanism. This Hub
deliberately retired external lead webhooks in favor of one lead pipeline
(`hub/leads.py`) — that is a documented, hard-won decision, not an
oversight, and is out of scope for this item.

## Verification

`test_landing_maker.py`, `test_landing_spec.py`, `test_landing_images.py`,
`test_landing_embeds.py`, `test_io_start.py`, `test_unwired.py`,
`test_thinking.py`, `test_ci_gate.py`, and the full static-check suite
(`tools/jscheck.py`, `checktemplates.py`, `linkcheck.py`,
`pagecheck.py --strict`, `integritycheck.py`, `spellcheck.py`) all pass.
CI on PR #473 was green before merge.
