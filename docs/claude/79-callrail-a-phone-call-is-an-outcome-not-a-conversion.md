## CallRail: a phone call is an outcome, not a conversion

`modules/reports/callrail.py`, `modules/reports/callrail_map.py`,
`/reports/callrail-check`, `/reports/provider-check/callrail`, and the
*Phone calls* tile on the client's page. Todd asked for callrail.com on the
reporting hub, and the reporting hub is the ad-performance fact table:
campaign-days with spend, impressions, clicks and conversions, one platform
per row. CallRail is none of those things. It is call tracking -- a
client's numbers ring through it, and every inbound call is a record
carrying the source that drove it (Google Ads, Google Organic, a billboard
number, Direct). So the question was not how to pull it but what a call
*is* once it lands beside a media buy, and the answer is the one the
module had already given twice: **a call is an outcome, the way a Suite
lead is and a GroundTruth visit is, and it is filed under its own name.**

**The shape is the GroundTruth pattern, and for the same reason.**
apidocs.callrail.com is one more host the Hub's own outbound proxy refuses
(it answered 403 to the CONNECT, like api.callrail.com beside it), so every
name in `callrail_map.py` is a transcription of what the reference says as
the search index shows it, marked PLACEHOLDER: the origin
`https://api.callrail.com`, the key as `Authorization: Token token="<key>"`,
an agency key seeing several accounts (`GET /v3/a.json`), each account
holding one *company* per client, and `GET /v3/a/{account_id}/calls.json`
listing calls between `start_date` and `end_date` in pages of up to 250
with the company, the source and the first-time flag among the optional
`fields`. The pull reads only the map; a correction on the check page
changes no line of it, and every name is overridable by environment
variable (`CALLRAIL_CALLS_PATH`, `CALLRAIL_AUTH_FORMAT`,
`CALLRAIL_FIELD_DATE` ...) so it can land without a deploy.

**The origin is documented and still never called unset.** The rule in
`docs/claude/53`, restated in `docs/claude/73` for GroundTruth's own
documented origin, is about who confirms the host rather than how good the
guess is: the pull runs nightly with nobody watching and the key rides in
a header on every request. So `CALLRAIL_API_BASE` has to be set before
`call()` builds anything, `missing()` names it beside the key, the status
line says *key set; CALLRAIL_API_BASE unset* rather than "not configured"
(which sends somebody to check a key that is set), an `http://` origin is
refused by name, and the check page prints the documented guess with
*Nothing was called* above it.

**What a call becomes.** The fact table is keyed on (platform,
account_id, campaign_id, date). For CallRail the *account* is the
**company** -- the CallRail object that is one client -- and the
*campaign* is the **source**, slugged (`Google Ads` and `google ads` are
one source), so a client's calls read *Google Ads: 12, Google Organic: 7,
Direct: 3* on the staff page and sum to one tile on theirs. Spend,
impressions, clicks and conversions are all zero on the row, and the
counts ride in `extras` under their own names: `calls`, `answered`,
`missed`, `voicemail`, `first_time_calls`, `good_leads` (the platform's
own lead marking) and `duration_seconds`. The company's name rides as
`advertiser_name`, which is what the auto-mapper reads for a likeness
(`account_name_v1`) -- a CallRail company is named for the business more
reliably than any campaign is -- and the filing is a proposal like every
other, confirmed by a person before it reaches a figure. Outbound calls
are counted and left out: a call the client's own staff placed is not a
lead.

**Never a conversion, and never a bar.** `conversions` is `None` in the
map and `callrail` is in `client_view.NO_CONVERSIONS`, for the reason the
visits write-up gives: a media platform's conversion is an action the
platform attributed to an ad, a call is a person picking up a phone, and
one figure holding both is a number nobody can explain to the client
whose page it is on. `store.OUTCOME_PLATFORMS` names `suite` and
`callrail` as the rows the client's page reads apart from the media
products -- no zero-impression bar, no table row, no investment line --
and the cost report skips them for the same reason a $0 platform column
would read as a media buy that cost nothing. The client's page draws a
*Phone calls* tile gated on a row that actually carries a `calls` count,
the visits tile's own rule, and data.json and the PDF carry it through the
same list the other tiles do. The reconcile names the platform as not
measurable, since there is no spend to reconcile.

**Reading is paged, and a read that stopped short says so.** The pull
follows `total_pages` up to `MAX_PAGES` per account and, past that, files
what it read and stamps the watermark with the account it stopped on
rather than reporting a clean night on a partial month. Calls read and
none filed is a failure by name, not an `ok` with zero rows -- the
StackAdapt finding in `docs/claude/49`, one platform over. A required
field whose name has been blanked is unresolved and named by the fact
column it leaves unfilled, and an unreadable `start_time` costs that call
and is counted, both from the GroundTruth review.

**The map is judged on whether a NAME is real, across a sample of calls
rather than on the first one.** `check_map()` originally asked `_dig()` of
`rows[0]`, and `_dig()` renders a missing key and a present-but-null key
alike as `None`. An unattributed call has no source, it is the first call
on the page as often as any other, and `source` is REQUIRED -- so one such
call at the top of the answer made the map read as broken, `to_facts()`
refuse every call in the window, and the error send somebody to correct a
name in `callrail_map.py` that was right. The same three calls filed
cleanly or lost the night on nothing but the order CallRail returned them
in. A wrong name is absent from *every* row, while a real field is simply
empty on some calls, so `_has()` asks whether the key is present at all
and `check_map()` asks it of `MAP_SAMPLE` (50) rows: resolved if any call
answers to the name. A name that is answered to and empty on every call
read still resolves -- the name is real -- and says so in `why`, since an
always-empty required field is worth a second look even though it is not a
map fault. `to_facts()` already filed a sourceless call as `no-source`;
it is the gate in front of it that disagreed.

**An override is a correction being tried, and the check page says which
ones are still owed to the file.** Every name is overridable by
environment variable so a correction lands without a deploy, and an
overridden name used to render on the check page exactly like a settled
one -- so once a variable was set, nothing anywhere said `callrail_map.py`
still disagreed with the running Hub. That is the shape of the
service-level value that quietly beats a linked env group (`docs/claude/03`)
minus the panel that reports it. `callrail_map.overrides()` names each one
with its variable and the value answering, `config()` carries the list, and
the page prints *n not settled* over a table of them, or says every name is
the one in the file. A variable set to the value already in the file is not
a disagreement and is not listed. The check page also runs the whole map
through `_redact_deep()` now: the map holds `{key}` rather than the key,
but an override is a person typing into Render, and `CALLRAIL_AUTH_FORMAT`
set to the finished header rather than the pattern would otherwise print
the key onto a staff screen.

**The check page masks the caller.** One page of yesterday's calls is
what it prints, and a call record carries the caller's name and number,
the recording and any note. Those are about a person, and a staff screen
is still not a place to print a stranger's phone number: `mask_row()`
replaces the value of any key naming a phone, a customer, a recording, a
transcript, a note or an email, and the page says so. The key names are
what the page is for, and they stay.

**Every request is recorded** under a `callrail` quota row that counts
requests and reads *not measured* against a limit until
`CALLRAIL_MONTHLY_LIMIT` is set, the marker in `_PROVIDER_MARKERS` is the
domain rather than a host because the origin is a setting, the nightly
job runs it last so a slow account list never delays a spend feed,
`/status` carries the key's row, `/diagnostics` has a check that reaches
no network, and `test_reports_callrail.py` asserts all of it on SQLite
and again on Postgres. The first live pull is where the transcription is
checked: set the origin, open the check page, paste the real names into
the map.

**What is deliberately not here.** Attribution finer than the source
(medium, campaign, keyword, the UTMs and click ids CallRail keeps on a
call) waits on a screen that would draw it; form submissions and text
messages are other outcomes with no tile yet; and a Windsor `callrail`
table is mapped as placeholder columns like every other provider table,
should the managed provider ever land one.

All OpenAI calls go through hub/ai.py with client= or brief=;
/api/integrity enforces it.
