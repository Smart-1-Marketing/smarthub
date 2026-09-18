## The Client 360 script, in files, not a template

`hub/templates/client360.html` carried the record's whole JavaScript
inline: 5,900 lines by September 17, 2026, in one `<script>` block, of
which only `render()`, the function that builds the card markup, needs
Jinja (its help dots, `user_name`, the Ask SmartHub chips). Every other
function was plain JavaScript that node could have linted as a file and a
test could have read as a file, and instead thirteen tests sliced blocks
out of the template by comment markers and thirty-five read the template
as text to ask what the record does.

The cut, one rule: **everything without Jinja moves; nothing with Jinja
does.** The inline script keeps `user_name`, `ASK_CHIPS`, `groups`, `run`,
`pick`, `render` and the `{% if q %}run();{% endif %}` line. The rest is
seven files under `hub/static/`, cut at top-level function boundaries in
the order they stood, so a diff of the move is a move:

| File | Holds |
|---|---|
| `client360-core.js` | `$`, `esc`, `money`, the request guard and fetch wrapper, notices, the Refresh button, the health strip, the warnings column and rail badges, the next-action line, the sections and the rail |
| `client360-cards.js` | social, coming up, gallery, traffic, images and uploads, brand, work, links, UTM, forms, requests, IO start, Suite match |
| `client360-people.js` | the group banner, the Smart 1 Internal row, the group modal |
| `client360-audience.js` | the target-audience card and the brand lookup |
| `client360-skills.js` | the ecommerce and email-creator cards |
| `client360-header.js` | the category pill, the scan screenshots and lightbox, `loadBrandAndWork()` |
| `client360-performance.js` | execution plan, ad performance, landing pages, Google listing, YouTube, email campaigns, pipeline, orders |

`hub/client360_assets.py` is the one list of them in load order, and it
stamps each `<script src="/assets/…">` with a short hash of their contents,
so a deploy that changes a module changes its URL and no browser serves
the previous record against the new server. The modules load before the
inline script and are only ever *called* by it, never called at load time;
top-level `let`/`const` in a classic script are shared with the scripts
after it, which is why `c360Generation` in core is the same binding
`render()` increments.

**What the tests read.** `client360_assets.source_text()` is the template
plus every module, in order. Every test that read the template reads that
instead, through a small `_c360_source()` helper each carries, so a check
that asks "does the record do X" reads the whole record and not the third
of it that happens to be inline. Three tests keyed on the file path had to
name the file the thing moved to: `test_image_download.py`'s full-asset
registry (the brand logo tile, now in the cards module; its sweep walks
the modules too), `test_thinking.py`'s wait marks (the brand lookup's
scan wait, now in the audience module), and `test_proposal_plan.py`'s
sections slice. `test_client360_layout.py` holds the list of modules
against the directory and against the template's script tags, in both
directions.

**Not moved on purpose.** `render()` is 2,400 lines and stays inline
because its markup is the Jinja. Cutting *it* means moving the help dots
and the chips out of Jinja and into data the page fetches, which is a
different change with its own reasons.
