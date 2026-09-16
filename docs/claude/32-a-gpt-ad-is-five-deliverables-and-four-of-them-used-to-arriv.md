## A GPT ad is five deliverables, and four of them used to arrive separately

`modules/gpt_ads` (`/tools/gpt-ads`) builds one GPT ad pack for a client and
hands ad operations a single ZIP. `hub/gpt_ads_spec.py` is the spec — the five
deliverables on their requirement sheet, the copy limits, the readiness gate,
the landing-page check, the AI prompts and the export — read by the module, the
brief and the manifest alike, the same way `hub/proposal_spec.py` is.

Nothing on that sheet is hard. What was slow is that it arrived in five places:
the image in a thread, the copy in a doc, the URL in an email, the brand
colours in somebody's memory and the offer in the client's own words three
weeks ago. A pack went over with four of the five, came back, and the launch
date moved.

**The image spec is in `hub/creative_specs.py`, not in the module.** 1:1 is
required so it is a `ratios` entry and a *fail*; 256x256 is recommended so it is
`min_size` and a *warn* — a 200px square runs, it just runs soft, and collapsing
those two into one warning is what teaches people to ignore warnings. That unit
is the one thing in the kit that is **not** transcribed from S1M CREATIVE SPEC
KIT 2025, so it carries `source` and `catalogue()` no longer claims the kit for
all of them. No file-weight ceiling and no format list are published for the
placement: none is invented, and the notes say so.

**The image is measured, never described.** The pixels are read off the bytes
with `hub.images.dimensions` before anything is stored, and a file that is not
1:1 is refused at the door with its measured size and a pointer at the resizer.
A form field saying "1080x1080" over a 1200x628 crop is the ordinary way a
rejected ad happens, and a red flag on a pack somebody exports anyway is not a
gate. An upload is stored **exactly as it arrived** — "provide the
highest-quality brand-approved version" is on the sheet, and re-encoding an
approved asset to save a few KB is how a logo picks up artefacts nobody
approved. Only generated squares go through `hub.images.optimise`.

**The landing page is fetched, not ticked.** "Confirmation that the page is
live and mobile-friendly" is a deliverable, so it is answered by requesting the
page: status, redirect chain, and whether the document declares a viewport —
reported as *declares a viewport*, not as *mobile-friendly*, because the first
is what was measured. A check that could not run says **not measured** and never
shows a tick, and changing the URL discards the check that belonged to the old
one rather than leaving a green light against a page nobody has fetched.

**The copy checks are code, and they are the Social Planner's.**
`hub/gpt_ads_spec.py` imports `MONEY_RE`, `PHONE_RE`, `DEADLINE_RE`,
`SUPERLATIVE_RE`, `PLACEHOLDER_RE` and `BANNED_PHRASES` from `hub/social_plan.py`
rather than restating them — same failure mode, so the next fix to those
patterns lands once. A price, a percentage, a phone number or a deadline that is
not in the offer or brand fields a human filled in is a **block**; superlatives
warn. So does an expiry date already in the past, except that one blocks:
shipping it means running something false.

**The character limits are ours and say so.** The sheet publishes none, so
`LIMITS` is house guidance, labelled as house guidance on the screen and in the
brief, and going over is a warning — never a block, because a block implies a
rejection nobody has published. Everywhere else the sheet is silent, the export
prints *not supplied* rather than a plausible value.

The ZIP carries the square, `ad-copy.csv`, a plain-text brief in the sheet's own
section order and `manifest.json`. When the image cannot be embedded the pack
still goes, with the reason at the top of the brief and in the manifest — three
files where there should be four is a difference ad ops assumes they caused.

There is **no JavaScript mirror** of the gate. Target areas and the creative
classifier each carry one and each needs a test proving the halves still agree;
that cost is paid twice already. Every save returns the server's own readiness
and the page renders what comes back. `test_gpt_ads.py` asserts all of it.
