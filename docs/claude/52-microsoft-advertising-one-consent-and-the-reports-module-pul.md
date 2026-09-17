## Microsoft Advertising: one consent, and the reports module pulls

`modules/ads_builder/bing_ads.py`, `modules/reports/bing.py`, the Connect
card on `/tools/ads/settings`, and the eighth row on `/diagnostics`'s OAuth
panel. Smart 1 Ads' own docstring has called Bing *phase two* since it was
written, `store.PLATFORMS` has carried `bing` since the reports module was
mounted, and `BING_AD_DEVELOPER_TOKEN` sat on Render read by nothing. What
was missing was the consent and the app registration -- both there now --
and the code between them.

**Four things every call carries, and a wrong one of any of them answers
the same bare failure.** The access token, the developer token, the manager
customer id (`CustomerId`) and, on a report, the account id. Microsoft's
answer to a wrong customer id is the identical `InvalidCredentials` a
revoked token gets, which is why `bing_ads.manager_id_problem()` refuses a
value that is not digits **by name before anything is sent**: the customer
*id* and the account *number* printed beside it in the UI are pasted the
wrong way round often enough that "BING_MANAGER_ACCOUNT_ID is
`X0123456`, which is not a customer id" is worth more than a 401. The
number is kept for the settings row and sent nowhere.

**The spellings are exactly as set on Render.** `BING_AD_CLIENT_ID`,
`BING_AD_CLIENT_SECRET`, `BING_AD_DEVELOPER_TOKEN`, `BING_MANAGER_ACCOUNT_ID`,
`BING_MANAGER_ACCOUNT_NUMBER` -- fields on `hub/config.py`, read at
call time through `config._s()` rather than once at import, and **no
`BING_ADS_` twin beside any of them**: `ALIASES` is only spellings in use,
and a speculative second name is how thirteen correct modules once became
findings.

**The app registration has two spellings in use, and only it.** The day
the first Connect was pressed (September 2026) Render carried the
registration as `MICROSOFT_ADS_CLIENT_ID` / `MICROSOFT_ADS_CLIENT_SECRET`
beside `MICROSOFT_ADS_REDIRECT_URI` and `MICROSOFT_ADS_TENANT`, and the
code read `BING_AD_CLIENT_ID` / `BING_AD_CLIENT_SECRET` only -- so the
settings card reported the pair missing with both plainly set, Connect
never appeared, and the callback opened by hand answered *"Microsoft did
not return an authorization code"*, which read as Microsoft failing when
nothing had been sent. Both spellings are in use, which is the one
condition for an `ALIASES` row: `bing_client_id` and `bing_client_secret`
name exactly the two, `BING_AD_` first, and `bing_ads.ALIAS_KEYS` reads
the pair through the same rows so the client and `/status` cannot
disagree. `env_report()` on `/diagnostics` says which spelling answered
and which was set and ignored. `test_reports_bing.py` now requires the
alias block to name exactly those two and no other Bing name. The
callback opened with no code says where sign-in starts and what a
redirect mismatch looks like.

**`MICROSOFT_ADS_TENANT` and `MICROSOFT_ADS_REDIRECT_URI` are read, both
optional.** The tenant is the identity platform's path segment --
`common` when unset, and a tenant id or `organizations` for a
registration limited to one directory, because `/common/` against a
single-tenant registration is refused (AADSTS50194) before any consent
screen; a value that is not a tenant shape (a pasted URL) falls back to
`common` rather than being sent inside the hostname path. The redirect
URI pins the callback to the exact string pasted into the Azure portal,
the `AMAZON_ADS_REDIRECT_URI` arrangement, for the deployment that
registered the `onrender.com` hostname rather than the domain; the panel
row carries `pin` so it prints the same string. A pin whose path is not
`/tools/ads/oauth/bing/callback` is **refused by name** on the card, on
`/diagnostics`, on the reports line and at Connect -- Microsoft would
send the code to a page this Hub does not answer -- and the code builds
from `PUBLIC_BASE_URL` meanwhile rather than sending the wrong string.

**The callback is `PUBLIC_BASE_URL`'s origin plus the mount's path, and it
is one reading.** `bing_ads.redirect_uri()` builds
`<origin>/tools/ads/oauth/bing/callback` from `config.public_base_origin()`,
and `hub/oauth_redirects.py` declares the flow with `source:
PUBLIC_BASE_URL` -- so the string the panel prints for the Azure portal is
the string the code sends, and `test_oauth_redirects.py`'s sweep holds the
two together (a flow that declares no builder fails there). Deliberately
**not** Google Ads' shape: that flow reads a whole-URL variable of its own,
which is the arrangement that once had the panel and the code disagreeing
about one string a console matches exactly. The registration has to allow
**personal Microsoft accounts as well as organizational ones**, because the
token endpoint is `/common/` and an advertiser's login is as often one as
the other; the consent asks `prompt=select_account` for the same reason,
since a silent re-use of whichever account is signed in is how the wrong
one gets connected with no error.

**The token lives where Google's does.** `BING_AD_REFRESH_TOKEN` in the
environment wins; the settings table (`bing_refresh_token`, written by the
callback) is the copy that works the moment somebody connects. The
connected page shows the token once against the pin variable, the way
Google's does, through the same `ads_connected.html` parametrized rather
than copied.

**The pull is the StackAdapt shape, not the Google one.** Reporting v13 is
asynchronous -- submit, poll, download a ZIP holding one CSV -- so
`modules/reports/bing.py` is `stackadapt.py`'s submit-poll-pending under
`BUDGET_SECONDS` on the shared scheduler thread: past the budget the report
is **pending**, not failed, nothing is stamped on the watermark, and the
next pull asks again; `POLL_TRIES` still caps the polls whatever the clock
says. **One report per pull**, scoped to every advertiser account the
manager can see (`Accounts/Search`, read each time, so an account added
next month is swept without anybody typing its id). Spend comes back in
the account's currency, not micros -- the divisor note in
`provider_map.py` is about Windsor's table, not this.

**`CampaignType` files the default product**, the `GOOGLE_CHANNEL_PRODUCTS`
rule one platform over: `products.BING_CHANNEL_PRODUCTS` maps Search to
Paid Search and Audience (the Microsoft Audience Network) to Programmatic
Display, and Shopping, DynamicSearchAds, Hotel and PerformanceMax take the
platform default with the mapping row saying nobody chose.
`default_for()` and `channel_decided()` read one `CHANNEL_PRODUCTS` table
keyed on the platform now, rather than a second `if plat == "bing"`.

**The reconcile reads the month back as an account report, labeled a
re-read.** `reconcile.bing_total()` submits an AccountPerformanceReport
over the same window on the same service, so it catches a day the restate
window never re-read and a campaign a scope left out, and cannot catch the
feed being wrong -- `independent: False`, said on the row, the rule
`stackadapt_total()` follows.

**Every call is recorded, and the token endpoint is not one.** A
`microsoft_ads` row in `hub/quotas.QUOTAS` counts calls (Microsoft charges
nothing per call and publishes no ceiling this Hub can read, so the row is
*not measured* against a limit until `MICROSOFT_ADS_MONTHLY_LIMIT` is set),
`_PROVIDER_MARKERS` matches the `api.bingads.microsoft.com` suffix both REST
hosts and their sandbox twins share, and `login.microsoftonline.com` is
deliberately outside it -- a token refresh is not a metered call.
`test_api_usage.py`'s pinned provider list gained the name, which is the
edit that check exists to force.

**Production or sandbox is named, never inferred from a refusal.** A
sandbox developer token answers only the sandbox host and Microsoft
publishes the tier nowhere an API can read -- Google's Explorer lesson --
so `BING_AD_ENVIRONMENT=sandbox` picks the host pair, and a refusal says
*"refused the credentials on the production host … a token issued for the
other environment answers exactly this; so does a revoked one"* rather than
reading as a bad key.

**Nothing here is exercised against a live account yet, and the module
says so.** The REST shapes are transcribed from the v13 reference and each
is read defensively -- a Success with no download URL, a poll status the
module does not know (read as Pending, never as finished, the HeyGen
lesson), a report with no column row, a ZIP with no CSV -- and the test
stands in for the platform at `bing_ads._http`. The first live pull is
where the transcription is checked; `list_accounts()`'s docstring names
which of its assumptions that is.

**What is deliberately not built is campaign management.** `/api/bing/*`
still answers 501, in words that say what *is* built, because a write path
nothing sells is the declared-and-unwired failure this file counts a dozen
of. `/diagnostics` gains a check that reaches no network -- off, warn
(credentials set, nobody has pressed Connect), ok, or an error naming a
customer id that is not one -- since the only authenticated call is a
token refresh against a manager account and the settings page and
`/reports/` already carry the pull's own last outcome.
