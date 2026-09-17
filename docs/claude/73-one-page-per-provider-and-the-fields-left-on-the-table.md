## One page per provider, and the fields left on the table

`modules/reports/provider_fields.py`, `/reports/provider-check/<platform>`,
the providers submenu on every provider-check screen, and the *API fields
not used* box on the three live check pages. `/reports/provider-check` was
the overview -- every platform's raw table against `provider_map.py` -- and
the AudioGo, GroundTruth and Amazon DSP check pages called an endpoint and
printed what came back. What none of them answered was the question a
person asks with a key in hand: which fields does the Hub read from this
platform, which does the platform offer that the Hub leaves unread, and what
do I set on the service. Twelve pages answer it now, one per platform, under
a submenu that lists the native pulls first and the Windsor-only platforms
after.

**The field map is read off the module that does the pull, never written
beside it.** `field_map()` returns `audiogo_map.config()["fields"]`,
`amazon_dsp.FIELD_MAP`, the StackAdapt query's paths, the Google GAQL's
columns, the Microsoft and Trade Desk CSV alias tables, and for a platform
with no pull the `provider_map.py` columns. A correction in any of those
files is a correction on the page; a second copy would have drifted the
way the provider-check placeholders drifted from Windsor's real columns.
Each map is labeled *placeholder until confirmed* or *read from a live
answer* by the same flag the pull itself carries (`CONFIRMED`,
`placeholder`), so the page cannot say a map is settled when the pull says
it is a claim.

**"Not read" is two claims, and the box keeps them apart.** *Answered and
not read* is measured: the keys a live check page saw on a row minus the
names the map reads (`unread_from_answer()`, dotted paths counted by their
root so `campaign.id` reads `campaign`), the columns a request asks the
platform for and then ignores (`requested_unread` -- Microsoft's
`AccountNumber` and `CampaignStatus` today), and the columns on Windsor's
raw table the map does not name (`unread_table_columns()`, *not measured*
while the table is absent). *Documented and not read* is a transcription
from the platform's reference, one block per platform in `DOCUMENTED`, each
naming its source and saying why the pull leaves those fields -- rates the
fact table computes itself, grains finer than the campaign-day, fee splits
that would double-count beside the billed cost -- so the next person does
not add a field the module refused on purpose. A transcribed list is what
the document says, not what an endpoint answered, and the page says so
above it.

**What Render has to carry is worded as what has to be true.** `ENV` is one
row per variable a native pull reads, required rows first, each with what
stays broken while it is unset. Nothing on the page says a variable is
missing: the Hub cannot read the service's environment or the env group
linked to it, and a value added to the service overrides the group's
silently (CLAUDE.md, the Render note). The pull's own `missing()` is the
measurement and is printed beside the list as *Status now*.

**AudioGo's header is no longer a placeholder.** The public FAQ
(`audiogo.com/faqs/api-reporting`) says the key is sent as `x-api-key:
your_api_key_here`, a bare key, and `audiogo_map.py` now defaults to that
rather than to a bearer token; agency customers request the key from
AudioGo Support, separate Dimensions and Metrics endpoints list what a
report may ask for, metric names are camelCase (`demandAudioImp` is
impressions; `geoCity` and `playerName` are dimensions), and a synchronous
report takes up to three dimensions. The origin and the report path are in
the spec PDF and on no public page, so `AUDIOGO_API_BASE` stays owed from
the spec. **GroundTruth's origin is documented and still never called
unset.** The public API's own examples call
`https://api-public.groundtruth.com`, and that is what `DEFAULT_BASE`
prints now; `GROUND_TRUTH_API_BASE` still has to be set to it before a
request is built, because the rule in `docs/claude/53` is about who
confirms the host, not how good the guess is. The endpoints are campaign,
ad group and creative timeseries by day, day of week and time of day;
metrics update daily and today's spend every two hours; the auth header is
on the Welcome page, which the Hub's environment cannot read, so it stays a
placeholder. Every one of those hosts -- audiogo.com,
api-docs.groundtruth.com, help.groundtruth.com, and the connector vendors
that list their fields -- is refused by the sandbox's egress policy; what
is here came through the search index's snippets, and the module says so
in each block's source.

**The index link kept its name.** The three check buttons that sat on every
Reports screen moved into the submenu, and the native-pull row on
`/reports/` links each check page under the name the button had (*AudioGo
check*, not the store's *AudioGO*) plus a *Field map* link to the provider
page. A dict key called `keys` cost the check pages a 500 on their first
render with an answer -- Jinja resolves `unread.keys` to `dict.keys` --
which is why the box's list is called `names`.
`test_reports_normalize.py` renders every provider page and the 404,
`test_reports_audiogo.py` reads the answered-and-not-read names off the
check page and pins the header, and `test_reports_groundtruth.py` pins the
printed origin beside the nothing-was-called notice.

All OpenAI calls go through hub/ai.py with client= or brief=;
/api/integrity enforces it.
