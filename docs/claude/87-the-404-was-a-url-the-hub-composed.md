## The 404 was a URL the Hub composed

`modules/reports/audiogo_map.py`, `modules/reports/audiogo.py`,
`/reports/audiogo-check`. On September 18, 2026 the check page reported:

```
HTTP 404 on /v1/reports/campaigns/daily: {"code":"not.found","message":"HTTP 404 Not Found"}
GET https://api.adswizz.com/domain/v8/reports/query/v1/reports/campaigns/daily
```

Read the URL rather than the status. `AUDIOGO_API_BASE` had been set, on the
service, to `https://api.adswizz.com/domain/v8/reports/query` -- the whole
report endpoint, correctly. `audiogo_map.REPORT_PATH` then appended
`/v1/reports/campaigns/daily`, a name this repository invented while the
spec PDF was outstanding and marked PLACEHOLDER in three places. Neither
half was wrong on its own. The concatenation was a URL nobody had ever
confirmed, and no field map, no header and no date parameter was ever going
to be reached through it.

**The environment could not undo it, which is the part that matters.** The
whole design of this module is that a correction lands from the Render
panel without a deploy -- `AUDIOGO_REPORT_PATH`, `AUDIOGO_AUTH_HEADER`,
`AUDIOGO_FIELD_SPEND` and the rest. But `_env()` read a blank value as
*unset* and handed back the module default, so `AUDIOGO_REPORT_PATH=""`
returned `/v1/reports/campaigns/daily`. The one override that existed to
express this correction was the one override that could not express it: the
only way to stop the Hub appending an invented path was to edit Python and
deploy. A configuration knob that cannot be turned to zero is not a knob.

**The base is now the URL, and nothing is bolted onto it.** `REPORT_PATH`
defaults to nothing, so `AUDIOGO_API_BASE` is called exactly as it is set
and the Hub invents no part of the address it sends a key to. Setting the
origin and the path separately still works; `AUDIOGO_REPORT_PATH` accepts
`-`, `/` and `none` as well as an empty value, because a blank environment
variable does not survive every settings panel, and for that one variable an
empty value is an answer rather than a silence. `url()` does not
deduplicate, repair or normalize anything beyond a trailing slash -- a base
that is wrong should be visible on the check page and named in the refusal,
not quietly rewritten into something the operator never typed.

**The origin stopped being a placeholder.** AudioGo's own *What is the base
URL for accessing the AudioGo API?* page names `https://api.adswizz.com/domain`:
AudioGo's reporting runs on AdsWizz's Domain API, which is why Todd's value
was an `api.adswizz.com` URL in the first place. That is `DEFAULT_BASE` now,
the treatment `docs/claude/73` gave GroundTruth's documented origin --
printed, and still never called until a person sets the variable, because
the rule in `docs/claude/53` is about who confirms the endpoint, not how
good the guess is. The report path below the origin is still only in the
spec PDF.

**A report there is a query, not a date range on a URL.** AudioGo describes
building one as *fetch the available dimensions and metrics, apply filters,
then issue a query combining filters, splitters and metrics* -- a request
body, which a `GET ?start_date=&end_date=` cannot express, and a plausible
second reason that endpoint answered 404. So `METHOD` joined the
placeholders and became overridable: `AUDIOGO_METHOD=POST` sends the date
parameters, the extra parameters and `AUDIOGO_BODY` (a JSON object, ignored
rather than raising if it will not parse) as the JSON body. The default is
still the GET that was tried -- nothing is guessed -- but the shape the spec
describes is now reachable from the panel. All AdsWizz times are UTC
regardless of the agency's own zone.

**A refusal names what it called.** `HTTP 404 on /v1/reports/campaigns/daily`
printed the one part of the URL that was a placeholder and hid the part a
person had set, so the error read as a platform problem. It is
`HTTP 404 on GET https://...` now, and a 404 or a 405 carries the sentence
that says the URL is `AUDIOGO_API_BASE` plus `AUDIOGO_REPORT_PATH` and that
`AUDIOGO_METHOD=POST` is the next thing to try. The check page prints the
two settings under the composed URL, and the request body when there is one.
The key is still nowhere in any of it.

**And the origin has to be able to carry the key.** `missing()` counts an
`AUDIOGO_API_BASE` that is neither https nor http-on-loopback as owed rather
than set -- the third finding in `docs/claude/53`, which GroundTruth's module
earned and this one had never been given. An `http://` origin here would have
handed the key, in a header, in clear, nightly, with nobody watching. The
sentence says *set, but not https*, because telling somebody a variable they
can plainly see is "unset" sends them to check the wrong thing.

`test_reports_audiogo.py` pins all of it: the base called as set, the path
cleared by each of its four spellings, the POST body over `AUDIOGO_BODY`,
the http origin owed, and the 404 naming the method, the URL and the two
settings that compose it.

All OpenAI calls go through hub/ai.py with client= or brief=;
/api/integrity enforces it.
