# Google Ads API — integration, and the approval ladder in front of it

What has to be true before Smart 1 Ads can price a campaign against Google's
own data and deploy one into a client's account, in the order each thing bites.
Read alongside `modules/ads_builder/api_readiness.py`, which asks these same
questions in this same order and answers them on screen.

The single point of this document: **"we have the API key" is four separate
claims**, and three of them fail in ways that look like a working
configuration.

---

## 1. The developer token is not one thing — it has a tier

A developer token is issued against the manager (MCC) account under
**Tools → API Center**. What matters is not whether it exists but which
**access tier** it carries, because the tiers differ in what they may call.

| Tier | How you get it | Accounts | Ops/day | Keyword planning |
|---|---|---|---|---|
| **Test** | automatic | test accounts only | 15,000 | no |
| **Explorer** | automatic, on application | production | 2,880 | **no** |
| **Basic** | applied for, reviewed | production | 15,000 | **yes** |
| **Standard** | applied for, reviewed | production | no cap | yes |

**Explorer is what a new token gets.** It is enough to read live campaigns and
to deploy one. It is *not* enough to measure a cost per click: the keyword
planning services are excluded, and `generateKeywordIdeas` comes back
`DEVELOPER_TOKEN_NOT_APPROVED` — a healthy token, refused for the tier.

That refusal is the trap. Read as a bad key it sends somebody to rotate a
credential that was fine, and swallowed into a `try/except` it leaves the
estimate showing the sector benchmark while a rep believes they are reading
Google. `keyword_plan.PlanningUnavailable` exists to keep those apart: it
carries `tier_needed` and its message names the application, not the key.

**Google publishes the tier nowhere an API can read it.** There is no field and
no endpoint. The only way to know is to make a planning call and read what
comes back, which is what **Settings → Check keyword planning** does.
`GOOGLE_ADS_ACCESS_LEVEL` and the stored setting are treated as *claims* and a
probe result outranks both — `api_readiness.tier()` says which it is going on.

> **Action:** apply for **Basic access** now. It is reviewed by Google on their
> timetable and there is a known application backlog. Everything except the
> measured CPC works at Explorer, so this does not block the rollout — it
> blocks one feature, and the tool says which.

---

## 2. The environment variables

Set on Render. `hub/config.py` accepts the alternate spellings; these are the
names `modules/ads_builder/google_ads.py` reads.

| Variable | What is missing without it |
|---|---|
| `GOOGLE_ADS_DEVELOPER_TOKEN` | the API cannot be called at all |
| `GOOGLE_ADS_CLIENT_ID` | the Google sign-in cannot start |
| `GOOGLE_ADS_CLIENT_SECRET` | the Google sign-in cannot start |
| `GOOGLE_ADS_REDIRECT_URI` | Google has nowhere to send you back to |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | the MCC to act through — needed for client accounts |
| `GOOGLE_ADS_REFRESH_TOKEN` | optional; pins the authorisation across redeploys |
| `GOOGLE_ADS_ACCESS_LEVEL` | optional; records the tier so a page can say it before anyone probes |

Two deployment-specific traps that have cost working features here before:

* **Render stores quotes literally.** `GOOGLE_ADS_DEVELOPER_TOKEN="abc"`
  includes the quote characters, and every call then fails authentication with
  a token that looks correct on the settings page.
* **A placeholder is worse than a blank.** `hub/config.py` detects the known
  placeholder strings for exactly this reason — a value like `YOUR_TOKEN_HERE`
  makes every "is it configured?" check answer yes.

The redirect URI must be listed verbatim on the OAuth client's **Authorised
redirect URIs**, as `<PUBLIC_BASE_URL>/tools/ads/oauth/callback`. A green
"configured" over the wrong OAuth client is a `redirect_uri_mismatch` in front
of whoever is doing the connecting.

---

## 3. Authorising, once, for the agency

**Settings → Connect Google Ads** runs the OAuth consent and stores the refresh
token. Sign in as the account that **owns or manages the MCC**, not as whoever
happens to be at the keyboard: the token inherits that person's access, and a
rep's personal login reaches whatever they personally can see.

`GOOGLE_ADS_REFRESH_TOKEN` in the environment always beats the stored copy,
because it is the copy that survives a redeploy. Set it once the connection is
working.

---

## 4. Reaching the client's account is a separate act, and it is theirs

This is the rung that surprises people. Authorising our side does not give us
the client's account. There is no "add this email" call. We send a **manager
account link invitation** from our MCC and **the client accepts it** in their
own Google Ads account under **Admin → Access and security → Managers**.

Until they accept, the API reports the account as simply not there — an empty
customer list, not an error. `api_readiness.preflight()` reads that correctly
and says *the link invitation has probably not been accepted*, with where to
look, rather than reporting a credential problem.

This has lead time measured in days and it is on the client's side of the
desk. Send the invitation when the estimate goes out, not when the deploy is
due.

---

## 5. The approval ladder

Deploying is the last rung of six, and the preflight checks all of them at
once. It refuses with **every** unmet condition named, not one per press: a rep
who fixes the status only to be told the account is unreachable has been round
the loop twice for something one screen could have said.

| # | Rung | Who | Where |
|---|---|---|---|
| 1 | Estimate approved internally | the rep who built it | proposal page |
| 2 | Material edits re-checked | the model, then the rep | second press on Approve |
| 3 | Estimate sent and answered | the client | `/tools/ads/estimate/<token>` |
| 4 | Proposal status set to APPROVED | approval hub | Approval Hub |
| 5 | API credentials, tier and account reachable | whoever holds Render | preflight |
| 6 | Deploy | the rep | proposal page |

Three rules inside that ladder:

* **An edit clears the approval.** A *material* edit — the budget, the
  audience, the do-not-target list, a removed keyword, a removed negative —
  sends the estimate back through the model before it can be approved again.
  That is two presses on purpose: the first returns the re-check, so a rep who
  quartered a budget sees what it did to the plan before the document they sign
  off is the one a client reads.
* **The client's answer is shown, and is not a blocker.** A rep may have an
  approval by phone. But "we never sent it", "they asked to talk first" and
  "they said yes" are three different situations, and the screen where somebody
  is about to spend a client's money is the right place to see which.
* **The dry run is never gated.** Validating is how you find out what is wrong,
  so gating it behind the conditions it exists to diagnose would make the
  diagnostic unavailable exactly when it is needed. `validateOnly` works at any
  status.

Everything Google does at deploy is a single atomic `googleAds:mutate`. If one
operation fails Google rolls the whole batch back, so a half-built campaign
cannot happen — and every campaign is created **PAUSED**.

---

## 6. What the measured CPC actually is

Two services, returning **two different numbers**. Conflating them overstates
every estimate this tool produces.

* **`generateKeywordIdeas`** → per keyword: average monthly searches,
  competition, and the **top-of-page bid** range (`lowTopOfPageBidMicros`, the
  20th percentile; `highTopOfPageBidMicros`, the 80th). That is what you would
  have to **bid** to show at the top of the page. It is **not** what you pay
  per click, and it is the higher figure.
* **`generateKeywordForecastMetrics`** → for the whole campaign at a stated
  bid, budget, geography and network: clicks, impressions, cost and
  **`averageCpcMicros`**. *That* is a cost per click.

`spec.CPC_SOURCES` holds all three possible provenances — `benchmark`,
`top_of_page_bid`, `forecast` — with the caveat each one must appear beside.
The estimate reads that rather than hard-coding a caveat, so a label cannot
drift from the call that produced the number under it. The forecast is
preferred; the bid range is the fallback and is labelled as a bid; the sector
benchmark is what you get when neither answered, labelled as it always was.

Three things the measurement will not do:

* **It will not price an area Google could not place.** An unresolved area is
  named on the document itself and is never widened to the state it sits in —
  a CPC measured across three of a client's five counties is not this
  campaign's CPC.
* **It will not run on page load.** It is a button. A keyword plan is a handful
  of operations against a daily cap the deploy also has to fit inside, and a
  CPC that re-fetched itself would change under a client mid-conversation.
* **It will not leave the tiers behind.** Measuring re-costs Good/Better/Best
  from the same number, keeping each tier's wording. A measured headline over
  tiers still costed at the sector rate shows a client two different campaigns.

---

## 7. Operations budget

Ops are counted per developer token per day, and they reset at midnight
Pacific. At Explorer's 2,880 that is a real ceiling; at Basic's 15,000 it is
comfortable. `hub/quotas.record_google()` already counts every call this module
makes — successful or refused, because a refused operation still spent one —
and `/diagnostics` compares the daily total against the ceiling.

Rough shape of what things cost:

| Action | Operations |
|---|---|
| Measure CPC for one campaign | ~3 (geo resolve, ideas, forecast) |
| Deploy a campaign | 1 atomic mutate |
| Read live campaigns | 1 per account per load |
| Planning check on Settings | 2 |

---

## 8. Rollout order

1. Set the four required variables on Render. Watch for quotes and
   placeholders.
2. Add the redirect URI to the OAuth client.
3. Connect Google Ads on Settings. Confirm `refresh_token_source` reads
   *environment* once `GOOGLE_ADS_REFRESH_TOKEN` is pinned.
4. Run **Check keyword planning**. Expect it to say Explorer — that is the
   normal answer for a new token, and it is not a fault.
5. Apply for **Basic access**. Measured CPC lights up when it lands, with no
   code change and nothing to switch on.
6. Send manager link invitations for the client accounts we will deploy into.
   This has the longest lead time of anything here and it is the one step
   somebody else has to complete.
7. Deploy the first campaign as a **dry run** (`validateOnly`) before a real
   one.

Until step 5 lands, `modules/ads_builder/export.py` is the working route: the
same approved campaign as a Google Ads Editor import file, posted under the
account owner's own sign-in, needing no API access at all.
