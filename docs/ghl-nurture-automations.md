# Lead-tag nurture automations — build instructions

**Audience:** whoever builds the workflows inside Smart 1 Suite (GoHighLevel).
**Status:** specification. Nothing here is created by code — see §1.3.

Every lead form in the Hub already writes a contact into Suite and tags it.
Nothing listens to those tags. This document says exactly which tag each form
produces and which nurture sequence should fire on it, so the workflows can be
built once and stop being guessed at.

Build order: **IMS Advertising Trade first** (§4), then the rest in the order
they appear.

---

## 1. How a tag actually arrives

### 1.1 The path

```
lead page  →  hub/leads.capture_and_deliver()      # row stored first
           →  hub/ghl_contacts.payload_for()       # builds the contact body
           →  POST /contacts/upsert                # Suite writes the contact
```

`payload_for()` sets the tags at `hub/ghl_contacts.py:242`:

```python
"tags": [t for t in ("smart1-hub", row.get("source") or "",
                     row.get("page") or "") if t][:10],
```

So **every lead arrives with up to three tags**:

| Tag | Value | Use it for |
|---|---|---|
| 1 | `smart1-hub` — on every single lead | Master audience + suppression anchor. **Never a trigger.** |
| 2 | the *source* — the tool (`calculators`, `hvac`, `scan_widget`) | Reporting, and group-level filters |
| 3 | the *page* — the specific form (`IMS Advertising Trade Calculator`) | **This is the trigger.** |

**Trigger on the page tag.** The source tag is shared by five calculators; it
cannot tell you which one someone used.

### 1.2 Six rules that govern every workflow below

1. **Copy the tag strings, never retype them.** The Hub sends the page title
   verbatim — `IMS Advertising Trade Calculator` — and Suite normalises tags to
   lowercase on write, so what you will see in the sub-account is
   `ims advertising trade calculator`. Confirm the exact stored casing with the
   test in §3.1 before building, then pick the tag from Suite's own
   autocomplete rather than typing it. A trigger tag that differs by one
   character fires never and looks correct on screen.

2. **A repeat submission is not expected to re-fire the workflow.** The upsert
   re-sends the same tag list, and a contact cannot hold the same tag twice —
   so *Tag Added* has nothing new to fire on. That is free de-duplication, and
   it is also why a lead who returns six months later gets nothing. Confirm it
   with the §3.1 test, then either accept it or add a second *Contact Changed*
   trigger with a date filter. Do not "fix" it by removing tags on exit; that
   breaks reporting.

3. **The contact carries almost no data.** The full set of fields written is
   first name, last name, email, phone, company name, website, address, city,
   state, postal code, source. **The calculator's own inputs are not sent** —
   not the trade value, not the cash budget, not the services ticked. So the
   merge fields available to you are:

   ```
   {{contact.first_name}}   {{contact.name}}   {{contact.company_name}}
   {{contact.email}}        {{contact.phone}}  {{contact.source}}
   ```

   Every email below is written to stay true with only those. See §11 for what
   it would take to lift that limit.

4. **`smart1-hub` is on everything, including signed clients.** Filter it out,
   never trigger on it.

5. **The Master Services Agreement is not a lead.** It fires when a client
   *signs*. It must be excluded from all nurture — see §10.

6. **Display Ad Builder prospects never reach Suite.** `hub/ad_builder_link.py`
   calls `leads.capture()`, not `capture_and_deliver()`. There is no contact
   and no tag, so there is nothing to build. Listed in §10 so nobody hunts for
   a tag that does not exist.

### 1.3 These must be built by hand

HighLevel's API has no create-workflow endpoint — the entire Workflows surface
in their published OpenAPI spec is one read call, `GET /workflows/`, scope
`workflows.readonly`. There is no POST, PUT, PATCH or DELETE. Automations are a
UI-only action, or they arrive inside a snapshot.

What *can* be driven from the Hub, once these exist:
`POST /contacts/{id}/workflow/{workflowId}` to enrol a contact,
`POST /contacts/{id}/tags` to tag one. Both need scopes the Hub does not
currently request (`hub/ghl_oauth.py:52` is read-only), and adding a scope
requires re-consent.

---

## 2. Tag inventory — every lead form in the Hub

Trigger strings are shown as Suite stores them (lowercase).

### Calculators — source tag `calculators`

| Slug | Trigger tag | Sequence |
|---|---|---|
| `trade` | `ims advertising trade calculator` | §4 — **build first** |
| `ctv` | `connected tv reach & budget calculator` | §5.1 |
| `digital-audio` | `digital audio reach & budget calculator` | §5.2 |
| `dooh` | `dooh reach calculator` | §5.3 |
| `female-18-34` | `female 18–34 market calculator` | §5.4 |

> **`female 18–34` uses an en dash (–), not a hyphen (-).** Typing a hyphen
> creates a trigger that never fires and looks correct on screen. Copy the
> string.

### Market-plan tools — one source tag each

| Source tag | Trigger tag | Vertical |
|---|---|---|
| `hvac` | `hvac market plan` | HVAC contractors |
| `legal` | `legal market plan` | Law firms |
| `restaurant` | `restaurant market plan` | Restaurants |
| `ski` | `ski resort market plan` | Ski resorts |
| `recruit` | `recruitment market plan` | Recruitment / staffing |
| `boat` | `boat dealer weather marketing` | Boat dealers |
| `rv` | `rv dealer demand plan` | RV dealers |
| `tourism` | `tourism marketing plan` | Tourism / destinations |
| `stadium` | `stadium to screen` | Stadiums / teams |

All nine share one sequence — §6.

### Abandoned-form variants

| Trigger tag | Meaning |
|---|---|
| `tourism marketing plan (partial)` | Started the tourism form, never finished |
| `stadium to screen (partial)` | Started the stadium form, never finished |

Short recovery sequence — §7. **Do not** put these into the full §6 sequence;
they never saw the plan the emails refer to.

### Other sources

| Source tag | Trigger tag | Sequence |
|---|---|---|
| `scan_widget` | *varies* — the widget's own tag or slug | §8 |
| `landing` | *varies* — the client name on the built page | §9 |
| `msa` | `master services agreement` | §10 — **exclude** |

`scan_widget` and `landing` do not have fixed page tags: the scan widget sends
`row.tag or row.widget_slug`, and a built landing page sends the client name.
Trigger those on the **source** tag (`scan_widget`, `landing`) and branch
inside the workflow if you need per-widget copy.

---

## 3. Settings every workflow uses

Apply this to all of them. Where a sequence differs it says so.

**Trigger**
- Type: *Contact Tag*
- Action: *Tag Added*
- Tag: the trigger tag from §2, lowercase, copied not retyped

**Trigger filters — all three, on every workflow**
- `Contact Tag` does **not** include `client-active`
- `Contact Tag` does **not** include `s1-stop-nurture`
- `Contact Tag` does **not** include `master services agreement`

The third one is the important one. A signed client who later fills in a
calculator would otherwise be sold to as a prospect.

**Workflow settings**
- Allow re-entry: **OFF**
- Stop on response: **ON** — email reply, SMS reply, and inbound call
- Sending window: Mon–Fri, 9:00–17:00 contact local time
- Time zone: contact's, falling back to the sub-account's

**First two actions, every workflow**
1. Add tag `nurture-active`
2. Assign to owner (round-robin the sales team, or a fixed rep)

**Last two actions, every workflow**
1. Remove tag `nurture-active`
2. Add tag `nurture-complete-<slug>` — e.g. `nurture-complete-ims-trade`

**Goal / exit event, every workflow**
Exit immediately on any of: a booked appointment, an opportunity created in any
pipeline, tag `s1-stop-nurture` added, or an email reply.

**Sender**
From the assigned owner, not a shared inbox. Reply-to the owner. These read as
one person following up, not a broadcast.

### 3.1 Verify the mechanism before building anything

Fifteen minutes here saves rebuilding nine workflows against a tag that never
fires. Submit **one** real test through the IMS trade calculator, using an
email address you control, then in the sub-account:

1. **Find the contact and read its tags.** You should see three:
   `smart1-hub`, `calculators`, and the page tag. Note the exact casing Suite
   stored — that is the string your trigger must match, and it settles rule 1
   above for every workflow in this document.
2. **Check the Source field.** It should read
   `Smart 1 Hub · calculators · IMS Advertising Trade Calculator`. If it does
   not, delivery is going down a different path and §1.1 no longer describes
   what is happening — stop and check `/api/integrity` and the Hub's lead
   panel before continuing.
3. **Submit the same form again with the same email.** The contact should be
   updated, not duplicated, and no new tag should appear. This confirms rule 2.
4. **Build the IMS workflow, then re-test with a second fresh address.**
   Confirm the workflow enters, Email 1 sends, and the exclusion filters in §3
   do not block it.

Only after step 4 passes should the remaining workflows be built.

---

## 4. IMS Advertising Trade Calculator

**Trigger tag:** `ims advertising trade calculator`
**Sequence length:** 5 emails over 16 days
**Objective:** book a trade-inventory conversation.

### Why this one is different

Every other form produces someone weighing a budget. This one produces someone
who has told you they have **assets instead of cash** and wants to know what
those assets buy. The calculator already showed them the split — Smart 1 work
(website, Suite, blogs, SNAP, social calendar, digital audit) can be structured
as 100% trade, while outside media (paid search, CTV, paid social, DOOH,
streaming radio) carries a cash component because a third party invoices us.

The calculator's own closing steps are the spine of this sequence:

1. Confirm the trade inventory and its retail valuation.
2. Set the monthly cash floor for outside media.
3. Sign the trade agreement before the first flight.

Emails 2, 3 and 4 each move one of those forward. **Do not lead with the cash
floor** — the reason they used a trade calculator is that cash is the
constrained side.

### Email 1 — immediately

**Subject:** Your trade structure, and the one number we still need
**Preview:** The valuation conversation takes about fifteen minutes.

> Hi {{contact.first_name}},
>
> Thanks for running the trade numbers. The split you saw is real: the work our
> own team does — website build, Smart 1 Suite, blog content, SNAP, your social
> calendar, the digital audit — can be structured as 100% trade. Media bought
> from an outside network is where cash comes in, because someone outside Smart
> 1 invoices us for it.
>
> The calculator can't do the next part, which is the part that decides
> everything: **what your trade inventory is actually worth at retail.** That's
> a fifteen-minute conversation, and it's the number the whole agreement is
> built on.
>
> Are you open to that this week or next?
>
> — {{user.first_name}}, Smart 1 Marketing

*Action after send: wait 3 days.*

### Email 2 — day 3 · the inventory

**Subject:** What counts as trade inventory
**Preview:** It's usually broader than people expect.

> {{contact.first_name}},
>
> The most common reason a trade conversation stalls is that the business
> assumes their inventory doesn't qualify. It usually does. We've structured
> trade against product, services, room nights, seats, event access, media the
> business already owns, and unused capacity.
>
> Two things make a valuation straightforward:
>
> **Retail value, not cost.** Trade is valued at what you'd sell it for, which
> is normally well above what it costs you to provide.
>
> **Timing you control.** We work to what your calendar can absorb — nothing is
> committed against a period that would hurt you.
>
> If you tell me roughly what {{contact.company_name}} would put on the table,
> I can tell you the same day whether it clears the Smart 1 side of your plan.
>
> — {{user.first_name}}

*Wait 4 days.*

### Email 3 — day 7 · the cash floor

**Subject:** The one part trade can't cover
**Preview:** And how small it usually has to be.

> {{contact.first_name}},
>
> Worth being straight about the cash side, because it's the question everyone
> gets to eventually.
>
> Trade covers Smart 1's work completely. Where a campaign buys inventory from
> an outside network — paid search, Connected TV, paid social, DOOH, streaming
> radio — that network invoices us in cash, so it has to be paid in cash. What
> trade *does* do there is offset our management fee on it, which is normally
> the part that makes a smaller media budget viable at all.
>
> So the practical question isn't whether you can run media on trade. It's what
> your monthly cash floor is. Once we know that and the inventory valuation,
> the plan writes itself.
>
> What would a comfortable monthly cash number look like for you?
>
> — {{user.first_name}}

*Wait 4 days.*

### Email 4 — day 11 · why the sequence matters

**Subject:** Sign before the flight, not during it
**Preview:** One scheduling note that saves a launch date.

> {{contact.first_name}},
>
> One process note, because it's the thing that most often costs a launch date.
>
> The trade agreement has to be signed before the first flight runs, not
> alongside it. Media is reserved against a signed agreement — if the paperwork
> is still moving when the flight is meant to start, the inventory we held goes
> back to the market and the date slips, usually by a few weeks.
>
> It isn't a long document, and there's nothing in it that gets negotiated
> after the valuation is agreed. It just has to come first.
>
> If you want to get this on the calendar, reply with two times that work and
> I'll send an invite.
>
> — {{user.first_name}}

*Wait 5 days.*

### Email 5 — day 16 · close the loop

**Subject:** Should I close this out?
**Preview:** No pressure either way.

> {{contact.first_name}},
>
> I haven't heard back, which is a perfectly good answer — timing is usually
> the reason.
>
> I'll stop emailing after this one. If trade becomes relevant again, the
> numbers you ran are still on file and we can pick it up from there rather
> than starting over.
>
> And if it's live but the timing was just wrong, tell me roughly when and
> I'll come back then instead.
>
> — {{user.first_name}}

*End of workflow. Apply `nurture-complete-ims-trade`.*

### Internal notification

After **Email 3** (day 7), if the contact has opened any email but not replied,
send an internal Slack or email notification to the assigned owner:

> Trade lead warm, no reply — {{contact.name}} at {{contact.company_name}},
> {{contact.email}} / {{contact.phone}}. Ran the IMS trade calculator. Worth a
> call.

A trade lead that opens three emails and never replies is a phone call, not a
fourth email.

---

## 5. The other four calculators

Same skeleton as §4 — 5 emails, days 0 / 3 / 7 / 11 / 16, same settings, same
day-7 internal notification. Only the argument changes. Emails 4 and 5 are
identical across all four except for the medium named.

### 5.1 Connected TV — `connected tv reach & budget calculator`

**Angle:** reach is not the problem; incremental reach is.

- **E1 (day 0)** — *Subject:* Your CTV numbers, and the gap in them.
  The calculator sizes the video buy. What it doesn't show is the audience CTV
  alone cannot reach. Offer a full plan.
- **E2 (day 3)** — *Subject:* The audience your CTV budget can't see.
  **Approved fact:** 79% of digital audio is consumed with no screen at all.
  Adding targeted digital audio alongside a CTV campaign drives a 21.8% gain in
  local market share and 11.5–13% incremental reach against video-only budgets.
- **E3 (day 7)** — *Subject:* What a CTV flight needs before it launches.
  Creative is the gate. A CTV buy with no spot behind it is a launch date
  nobody hits — production is quoted before the flight, not after.
- **E4 (day 11)** — *Subject:* Booking a flight, in order.
- **E5 (day 16)** — *Subject:* Should I close this out?

### 5.2 Digital Audio — `digital audio reach & budget calculator`

**Angle:** the attention/budget mismatch.

- **E1 (day 0)** — *Subject:* Your audio numbers.
- **E2 (day 3)** — *Subject:* One in five minutes, one sixteenth of the budget.
  **Approved fact:** digital audio takes 1 in every 5 minutes of consumer
  digital attention but historically receives about 1/16th of ad budgets.
- **E3 (day 7)** — *Subject:* Audio and CTV together.
  Same 21.8% / 11.5–13% figures as §5.1, argued from the audio side.
- **E4 / E5** — as §5.1, with "spot" meaning the audio creative.

### 5.3 DOOH — `dooh reach calculator`

**Angle:** DOOH sets up the rest of the plan; it rarely closes alone.

- **E1 (day 0)** — *Subject:* Your DOOH reach.
- **E2 (day 3)** — *Subject:* What DOOH is good at, and what it isn't.
  Frequency and presence in a defined geography. Pair with a retargeting layer
  or the impression is spent and gone.
- **E3 (day 7)** — *Subject:* Choosing the boards.
  Placement, not budget, decides the outcome. Offer the plan.
- **E4 / E5** — as §5.1.

### 5.4 Female 18–34 — `female 18–34 market calculator`

> Trigger tag contains an **en dash**. Copy it.

**Angle:** a demographic, not a medium — so the answer is a channel mix.

- **E1 (day 0)** — *Subject:* Reaching 18–34 women in your market.
- **E2 (day 3)** — *Subject:* Where this audience actually is.
  Split across social, streaming audio and CTV; a single-channel plan reaches a
  fraction of them at high frequency, which is the expensive way to be ignored.
- **E3 (day 7)** — *Subject:* What we'd run first.
- **E4 / E5** — as §5.1.

---

## 6. Market-plan tools — one sequence, nine verticals

**Trigger tags:** all nine listed in §2.
**Build:** one workflow per vertical (so the copy can name the trade), or one
workflow with a nine-way branch on the source tag. One per vertical is easier
to edit and easier to report on.

**Shape:** 4 emails over 12 days. Shorter than the calculators — this lead has
already read a whole plan, so the job is to get a person in front of them, not
to re-teach the material.

| Day | Subject | Body |
|---|---|---|
| 0 | Your {{PLAN NAME}} — and the part that isn't in it | The plan is built from general market data. What it can't know is *their* current spend, their seasonality and what's already working. Offer 20 minutes to make it specific. |
| 3 | The three things that change this plan | Whether they're retargeting; whether they're findable in AI search; whether their website converts what the plan sends it. Ask which of the three is weakest. |
| 7 | {{VERTICAL PROOF POINT}} | One concrete result or mechanic for the vertical — see the table below. |
| 12 | Should I close this out? | Same close as §4 Email 5. |

**Per-vertical substitutions**

| Source | Plan name | Day-7 proof point |
|---|---|---|
| `hvac` | HVAC Market Plan | Demand is weather-driven; budget that flexes with the forecast beats a flat monthly spend |
| `legal` | Legal Market Plan | Practice-area intent is narrow and expensive — being cited in AI answers matters more here than in any other vertical |
| `restaurant` | Restaurant Market Plan | Local map and review signals move covers faster than paid reach does |
| `ski` | Ski Resort Market Plan | A compressed season means the flight has to be booked before conditions, not after |
| `recruit` | Recruitment Market Plan | Candidate supply is a reach problem, not a conversion problem — passive audiences need audio and CTV |
| `boat` | Boat Dealer Weather Marketing | 60-30-10: inventory starts conversations, brand trust finishes deals |
| `rv` | RV Dealer Demand Plan | Same 60-30-10 argument — listing sites force you to compete on price alone |
| `tourism` | Tourism Marketing Plan | Booking windows decide flight timing; reach spent outside the window is spent twice |
| `stadium` | Stadium to Screen | The in-venue audience is already yours — the value is reaching them the other 360 days |

**Approved 60-30-10 wording** (boat and RV, and any other transaction-heavy
retailer): transaction-heavy retailers commonly spend 70–95% of budget on
third-party listing sites, which forces them to compete as a commodity on
price. A 60-30-10 split — 60% inventory demand capture, 30% brand awareness,
10% trust and reputation — lowers cost per acquisition and protects margin.

**Approved AI-search wording** (legal, restaurant, and anywhere the day-3 email
raises it): search is becoming an answer economy, where users increasingly take
the generated answer rather than clicking a link. Being cited requires accurate
schema markup, fast page loads, and clean directory synchronisation across 50+
local platforms.

---

## 7. Abandoned forms — the partial variants

**Trigger tags:** `tourism marketing plan (partial)`,
`stadium to screen (partial)`

These people gave contact details and then left before the plan rendered. They
have not seen it. **Two emails, and no selling.**

- **Day 0, within minutes** — *Subject:* Here's the plan you started
  One line, one link back to the tool, nothing else. The most likely reason
  they stopped is a distraction, and the highest-value thing you can do is make
  it one click to come back.
- **Day 2** — *Subject:* Want me to just send it over?
  Offer to run it for them and email the result. Ask for the one input the form
  needed.

If they complete the form, the full tag fires and the §6 sequence takes over —
so add an exit goal on the completed tag to stop these two from overlapping.

---

## 8. Scan widget — `scan_widget`

**Trigger:** source tag `scan_widget` (page tags vary per widget).

This lead ran an AI-visibility scan on their own site and has a score and a
findings list. That is the strongest hook in the whole set — the email can
refer to something specific about *their* site.

**Caveat:** the score and the findings are captured in the Hub but are **not**
written to the Suite contact today, so the workflow cannot merge them in. Until
§11 is done, the emails must refer to "your scan" without quoting the number.

4 emails over 10 days:

| Day | Subject | Body |
|---|---|---|
| 0 | Your scan results | The report is theirs; the fixes are ranked by what moves visibility fastest. Offer to walk it through. |
| 2 | The fixes that move the score most | Schema markup, page speed, directory consistency across 50+ platforms. Concrete, in that order. |
| 5 | Being cited, not just ranked | The answer-economy wording from §6. |
| 10 | Should I close this out? | Standard close. |

**Internal:** notify the owner on day 0 for any scan lead — these convert
better and faster than any other source in this list, and a same-day call
outperforms the entire sequence.

## 9. Built landing pages — `landing`

**Trigger:** source tag `landing`. The page tag is the client's name, so it
varies per page and cannot be triggered on generically.

These are pages built for a specific campaign, so a generic nurture is usually
wrong. **Build one workflow that does two things and nothing else:**

1. Notify the assigned owner immediately, with the page tag included so they
   can see which campaign produced it.
2. Send one acknowledgement email confirming someone will be in touch today.

If a particular landing campaign wants a real sequence, build it against that
page's own tag and add the client name to §2.

## 10. Excluded — do not build nurture for these

| Source | Why |
|---|---|
| `msa` / `master services agreement` | Fires when a client **signs**. This is an onboarding trigger, not a nurture one. Build a separate onboarding workflow, and make sure the tag is in every nurture workflow's exclusion filter (§3). |
| `display_ads` | Never reaches Suite. `hub/ad_builder_link.py:499` calls `leads.capture()` rather than `capture_and_deliver()`, so no contact and no tag is created. |

Recommended: the MSA onboarding workflow's first action should add tag
`client-active`, which §3 already filters on. That closes the loop — a signed
client cannot be dropped into a prospect sequence from any other form.

---

## 11. The known gap, and what closes it

Every sequence above is written to be true with six merge fields, because six
merge fields is all the contact carries. What is captured by the Hub but never
reaches Suite:

| Lost on the way to Suite | Would enable |
|---|---|
| Calculator inputs (trade value, cash budget, services ticked) | "the $5,000 in trade you entered" instead of "your trade" |
| Scan score and top issues | Naming the actual finding in email 1 — the single biggest lift available here |
| `pdf_url` — the generated plan or report | Re-sending the document from inside the workflow |
| `meta` (widget, calculator slug, lead value) | Branching and lead scoring without string-matching tags |

All four are already on the lead row in `hub/leads.py`. Closing the gap means
adding a `customFields` block to `payload_for()` in `hub/ghl_contacts.py` and
creating the matching custom fields in the sub-account — roughly a day's work
including the field setup, and it makes every sequence above materially
stronger.

**Build the workflows first.** They work as written. Treat the custom fields as
the next iteration, not a prerequisite.
