## Declared and never wired

The single failure this codebase has paid for most often, and there was no
check for it until now. Every instance is written up above and each cost a
feature that looked complete from every screen: Page Image Optimizer's
`>>> INTEGRATION POINT <<<` naming three writers that have never existed;
`hub/storage.manifest()`, whose docstring says it "feeds the orphaned-asset
audit" for an audit that had never been built; `io_creative` sitting in
`filing.KIND_LABELS` with no writer; `simvoly_client.check_limits()` written
with no caller while the page that needed it 500'd on every visit;
`TICKET_CREATE_FIELDS`, `TICKET_MANAGE_FIELDS` and `update_ticket()` existing
unused while the Hub wrote four of a ticket's eight fields; and
`openai_service.write_runway_prompt()`, written and uncalled until the button
was built. None of them errored, which is the whole difficulty — an uncalled
function is indistinguishable from a working one until somebody goes looking
for the feature it was half of.

`test_unwired.py` is the sweep. Four rules on it.

**It is an allowlist, not a rule.** A thin client over somebody else's API is
reasonably kept whole, and `check_limits()` is the proof in both directions:
it was unwired *and* it was exactly what was needed. So every survivor carries
the reason it survives, which is what makes the next one somebody adds a
decision rather than an accident. Held to
`check_stale_json_exemptions()`'s discipline: an entry naming a function that
is gone, or one something now calls, **fails**.

**Textual, not a call graph.** This repo reaches functions from Jinja, from
JavaScript, from an entry in a table and through `getattr`, and a call-graph
walk would report every one of those as unwired. A name appearing nowhere else
in any file is the only claim that survives all four. Decorated functions are
skipped entirely — a route is called by its framework, and naming it nowhere
is normal.

**Whole words, and one pass.** `name in src` per name per file is 1,700 names
against 1,500 files and takes the best part of a minute, which is a check
somebody drops from CI; counting identifier tokens once is six seconds. It is
also the more honest question, and it earned that immediately:
`assign_customer` had been reading as referenced because **`unassign_customer`
contains it** — the substring trap this file already names about `.btn`
matching `subtle`.

**It excluded its own allowlist by accident, first time out.** `ALLOW` names
every survivor as a string, so counting this file as a reference made the
sweep report a clean nothing — which is exactly how
`unmirrored_json_writers()` came to exempt each scanner, its test being
`"jsonstore" not in src` while every one of them explained jsonstore in its
own prose. It skips itself, it is handed a function nothing calls and required
to name it, and it started green.

**And prose was counting as a call site.** The first pass scanned `.md` too,
so the paragraph you are reading — which names `assign_customer` to explain
why it is allowed — read as somebody calling it and silenced the finding it
was written about. That is `hub/config.py`'s drift rule inverted: there,
matching text reported a docstring explaining the fix as the defect. Code and
templates only; a function is not reached from a Markdown file. Excluding it
immediately surfaced two more, both of which had been masked by their own
explanations: `qrcode_service.is_available()`, whose docstring said *"asked by
the CTA route"* when the route reads the reason off `generate_qr()`'s own
result instead — the better answer, since a separate pre-check is a second
reading of one question — and `google_client.verify_ga4()`, which needs an
agency GA4 token nobody holds.

**Three came out rather than in.** `hub/target_areas.running_zips()` was
byte-for-byte `all_zips()` — two readings of one question, which is the drift
this codebase names most often. `hub/ai.usage_summary()` was a second
reading of the by-module OpenAI spend `/api/quotas` already reports from the
same log. And `hub/access.may_view()`'s docstring said it was "the whole
access rule, in one expression that **both call sites read**" while neither
did — a shared rule that nothing shares is worse than no shared rule, because
the next reader believes it.

**One entry was a finding rather than an exemption, and it has been
removed.** `modules/sites_admin/seed_boot.py` promised that a freshly-recreated
database *"repopulates itself on the next startup with no manual
paste/upload"* — and it was imported by nothing, there is no
`seed/portfolio.json` for it to read and there never has been, so it had never
run and could not. The allowlist said REPAIR THIS OR REMOVE IT and named both
halves; repairing it needs a seed file exported from the live portfolio, which
is not a thing a check or a cleanup can conjure.

So the promise is gone rather than left standing. It is the same failure as a
report that answers zero when it could not look, in a docstring: **the Sites
Admin portfolio does not repopulate itself, and the file saying otherwise was
the only reason anybody would think it did.** That matters here more than most
places, because this Hub's own backup rule is about exactly this — the Render
disk is not backed up, and a plan change, region move or resize hands back an
empty one. Recovering that table is a management-panel *list-projects* export
imported through Sites Admin's own inventory import, by hand, and nothing in
the boot path shortens that. Wiring a seed at boot would also be a database
write in **two** gunicorn workers, which is the `create_all()` advisory-lock
problem one layer up, so it is not a one-line restoration either.

Removing it is checkable rather than remembered: the allowlist entry went with
the file, and `test_unwired.py` fails on an entry naming a function that is
gone — so the removal cannot be half-done.
