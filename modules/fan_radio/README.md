# Fan Radio

Football-themed radio spots in three dayparts — **Pre-Game Prep, Game Day,
Post-Game** — at every length the Radio Ad Creator sells, with a music bed
under them and a customer-facing page where the client listens, approves, or
asks for changes.

Mounted at `/tools/fan-radio`. It carries the same tools as the Radio Ad
Creator because it **reads the same modules**, rather than being built to
resemble them: `hub/radio_spec.py` for the lengths, the budgets, the read pace,
the bed vocabulary, the mix levels and the checks; `hub/radio_script_qc.py` for
the named script panel; `hub/voice_casting.py` for the casting question and the
scoring; `hub/radio_share.py` for the client link.

That is a fix rather than a tidy-up. Every one of those used to be a local
copy here, and three of them had drifted — the versions of this file above
said "same word budgets" and "same casting" while a :15 was 30–38 words here
against 35–42 there, and the casting question was missing two of the five
answers the shared picker offers. Each screen was internally consistent, so
the only way to notice was to open both.

---

## The script is checked before a voice is paid for

`hub/radio_script_qc.py` is a panel of named checks that runs on the copy, and
it is **the Radio Ad Creator's panel** rather than one written to match it.
This tool had two of its nine: a word count against the budget and the
trademark scan below. So a :30 that never said the client's web address was a
named finding one tool over and silence here, a required disclaimer that
quietly did not make the cut was nothing at all — there was nowhere to type
one — and the way to find out a :30 read short was to spend the ElevenLabs
characters and listen to the dead air.

Eleven rows now: the brand, the address and the number (`hub/script_contents.py`,
the same reader the Commercial Builder's CTA check uses); the read estimate and
the word budget; a disclaimer reproduced word for word; a price, deadline or
number that traces back to nothing anybody typed (`hub/social_plan.validate_copy`,
the reader the Social Planner and GPT Ads already share); the brand said often
enough for the length; stage directions nobody meant to be read aloud; the
beats; and the two rows that are this tool's own — the **trademark** verdict
and the **post-game result** rule, which are rows on this panel as well as the
prose they have always printed above the copy. The prose is the summary and the
panel is the detail behind a click; what changed is that the nine checks a rep
could not see are now beside the two they could, rather than the two looking
like the whole list.

**What may refuse a record is decided by certainty, not severity.** A
registered mark, a missing disclaimer, an invented price and an address the
read never says are facts about the text and they refuse a render with a 422.
A read estimate is words divided by a read pace, so it reports loudly and the
render still goes — refusing a correct read is how a panel comes to be switched
off, and switching this one off would cost the trademark check with it.

**And a :10 is neither asked for a response nor judged for leaving one out.**
The shared length table has said since it was written that ten seconds "cannot
carry a response somebody acts on"; nothing read it, so the content check would
have demanded the whole web address in a sponsorship tag. `carries_response` on
the length row is what the panel and the writing prompt both read now.

---

## The three things this does that a script generator doesn't

### 1. Nobody's trademark leaves the building

`phrases.py` carries 125 blocked marks — 32 club nicknames, 28 college
nicknames, league and broadcast package names, bowl and playoff marks, and
the fan slogans clubs actually register (Who Dey, Terrible Towel, 12th Man,
Bills Mafia, Roll Tide…). Every script is scanned three times:

- when it's written (the model is re-asked once, naming exactly what it
  broke),
- when it's hand-edited,
- immediately before a render is paid for.

A blocked hit **fails**: the Record button refuses and says which word.

The project's own team is entered as **context, never copy**. "Cincinnati
Bengals" tells the writer which market and schedule it's writing around, and
every word of it is added to that project's block list. Three-word names also
block the school portion — "Kansas State Wildcats" blocks *Kansas State* too.
A bare city is left alone, because a city is a place, not a mark.

A second, softer list flags phrases that are widely used as workarounds but
still draw attention — "the big game", "official sponsor of". Those surface
for a human call rather than failing.

### 2. Post-game spots are result-neutral by default

A spot booked to air after the final whistle is written and voiced days
earlier. It cannot know the score. Copy that quietly assumes one — "after
that big win", "we're rolling", "tough loss" — is flagged on any neutral
post-game script.

Two optional alternates exist for when the result *is* known: **:30 if it
went well** and **:30 if it didn't**, which the station swaps in. On those,
the same language is allowed.

### 3. The client approves in one place

One share link per project. Random 24-byte token, no login, `noindex`.
The customer sees each spot grouped by daypart with the audio, the script,
**Approve this spot** and **Request changes** with a comment box, plus
*Approve everything* and a general comment box. Feedback lands back in the
builder against the spot it belongs to.

Turn the link off, or issue a new one — the old link dies immediately.

---

## What it has that a script generator does not

* **Every length.** :10 sponsorship tag, :15, :30, :60 — the shared table's
  own menu, with the read floor on the two lengths that are bought by the
  second and the cost warning only on the :60, which is about twice a :30 in
  ElevenLabs characters every time it is re-recorded. The **:15/:30 pair is
  still the default**: ticking four lengths across three dayparts is twelve
  billed writes on a job that usually wants six.
* **Casting you can argue with.** The five characteristics are ranked against
  what ElevenLabs publishes about each voice, each row printing the words it
  matches on. The model's recommendation lands *in* the pickers, so the next
  press is a rep disagreeing with it rather than starting again. A voice can
  be named by ID instead — a cloned voice carries no labels for the ranking to
  score. Cloning itself lives in the Radio Ad Creator: it makes a voice out of
  somebody's recordings, which is a consent question, and one place to answer
  it is the right number.
* **A response number and a disclaimer, where the job has them.** Both are on
  the intake because the panel checks for them: tick *the read has to say it*
  beside the phone number and a script that leaves it out is a finding before
  the record, and a disclaimer is matched **word for word**. Neither field
  existed here, which is why neither check could.
* **The beats, where a rep can see them.** The shape a length is planned around
  is drawn beside the copy as well as stated in the prompt. Until it was, a
  script that had wandered from the plan read exactly like one written to it.
* **Reusable reads, shared with the Radio Ad Creator.** A picker on every spot
  offers the saved library, and *save as a reusable read* adds to it. The
  library is `hub/radio_presets.py` and both builders read the one store, so a
  read saved here is offered there. It is saved with this client's name put
  **back** to a `{business}` placeholder and filled in again for whoever the
  next project is for, because a library that bakes in the first client's name
  stops being reusable on its first save. This existed here with four reads, a
  store, a route and a test, and no screen in either tool — a route nobody can
  press is not a feature.
* **How the name is said.** Pronunciations apply to every spot on the project,
  and *show me what the voice reads* prints the copy ElevenLabs is actually
  handed. Without that line a pronunciation that is not taking looks identical
  to one that is, and the other way to find out is to spend a render.
* **A bed, and a real one.** Composed by ElevenLabs at the spot's own length —
  so nothing is trimmed to fit — or a licensed track uploaded. A spot with no
  bed is a straight read and passes the checks as one.
* **Somebody's own read.** A client with their own talent uploads the
  recording; it lands on the same fields a rendered read does, except that its
  length is honestly *not measured* where a rendered one is.
* **And a way out when that read runs long.** A read this tool recorded and
  that overruns has two levers already on the screen — tighten the script, or
  drop the voice speed, and record it again. An uploaded read has neither: the
  talent has gone home. So when the mix comes back over its slot, the panel
  works out the **playback rate** that would land it back inside, says what
  that costs in pitch, and re-renders on approval. Past **1.15×** it offers
  nothing and says how many seconds have to come out of the read instead.

## Flow

1. **Who it's for** — client (type-ahead over the Hub registry) or spec spot.
   Business name, website, offer, local team for context, tone.
2. **The brief** — the site is read once and turned into what a spot needs.
   Every field is editable; must-says are enforced verbatim.
3. **Write the spots** — six by default (three dayparts × two lengths), plus
   the two post-game alternates if you want them. Optional steer per batch.
4. **The spots** — word count against the budget, trademark verdict, football
   language detected. Save edit / Rewrite / Tighten / Record / Delete, with
   full version history.
5. **The voice** — pronunciations, the five casting characteristics, a
   shortlist with its reasons, a voice by ID, and what is left of the month's
   characters. Renders report **measured** runtime from `/with-timestamps`, or
   say "estimated" when they can't.
6. **Background music & the mix** — compose or upload a bed per spot, pick the
   level, render the mix in the browser, read the checks, file it.
7. **Send it to the client** — headline, intro, optional CTA button, link on.
   The client hears the **finished mix** where there is one and the raw read
   where there is not, and the page says which.
8. **File it in the Suite** — once the client has approved, one press puts the
   audio, the script and who signed it off on their opportunity in Smart 1
   Suite. Pressing again revises that opportunity rather than opening a second.

## Word budgets

`hub/radio_spec.DURATIONS`, read rather than restated — the same table the
Radio Ad Creator writes to, so a script genuinely does move between the two
without re-timing.

| Length | Words | Read floor | What it is for |
|---|---|---|---|
| `:10` | 22–28 | — | Sponsorship tag against a live read |
| `:15` | 35–42 | — | One message, one call to action |
| `:30` | 65–85 | 25s | The workhorse |
| `:60` | 140–170 | 54s | Room for a story rather than an offer |

Over-budget scripts are **flagged with the overage and re-tightened, never
truncated** — trimming clips a word off the end of the phone number. The floor
is on the two lengths bought by the second, because a read that lands well
under one of those is dead air somebody paid for; a floor on a tag would refuse
correct copy.

## The mix, and what "measured" means

There is no ffmpeg, ffprobe, pydub or numpy in the Hub's runtime. So the bed is
**composed to length** rather than trimmed, and the mix is rendered **in the
browser** through the Web Audio API, which ducks the bed under the read and
hands back a WAV. A WAV states its own sample rate, channel count and data
length in its header — so the duration filed against a spot is arithmetic on
the bytes we stored, measured by us, rather than a number the page reported.

An uploaded MP3 is the opposite case and says so: it is at a bitrate nobody
here chose, so its length is **not measured** — never a number, never zero.

The dB pair, the fades, the duck timings and the sample rate all come from
`/api/mix/config`, which reads the Commercial Builder's one music table. A
radio spot and a video spot duck their beds by the same amount, and the level
this panel shows is the level that renders.

A bed shorter than the spot is **reported, never looped** — a loop puts an
audible seam in the middle of a client's commercial. A read that overruns is
**never trimmed** — the mix renders at the longer of the two and comes back
measured and over, which is what the length check is for.

## Time compression, for the one read that cannot be recorded again

The length check used to be a dead end for an **uploaded** read. It said the
mix was 2.1s over a :30 and stopped the filing, and there was nothing on the
screen that could act on it: the talent who made the file has gone home, and
there is no ffmpeg here to re-time it with.

What there is, is the pipeline the mix already runs in. `playbackRate` on the
voice source plays the read faster, and a station's own playout does exactly
this — time compression is how a :32 read makes a :30 log. So the panel offers
the rate:

* **`hub/radio_spec.speed_suggestion()` works it out**, not the page — same
  rule as the dB pair. `vo ÷ (slot − lead-in)`, rounded **up** to the
  hundredth so the mix lands at or inside the slot rather than one rounding
  short of it. The bed's 0.3s lead-in is taken off the runway where there is a
  bed and not where there isn't.
* **It says what it costs.** `playbackRate` resamples, so the read gets shorter
  *and higher* — there is no pitch-preserving time-stretch in this runtime and
  this Hub does not add a library from a CDN for one feature. The offer quotes
  the semitones. Under **1.05×** is named as the rate nobody hears; between
  that and **1.15×** as audible on a close listen and inside what a station
  does.
* **Past 1.15× it offers nothing.** "Speed it up" is not an answer to a read
  five seconds too long. It says how far over the mix would still land at the
  ceiling and roughly how many seconds have to come out of the read itself.
* **Only an uploaded read is offered it.** A read recorded here gets the note
  that it has a re-record to ask for, because that is the better answer.
* **The rate is recorded on the mix.** A mix that only fits because it was sped
  up reads, on the panel, exactly like one that landed on the clock — so the
  length check's own row says `time-compressed to 1.08x (+1.33 semitones)`,
  and the filed record, the download filename and the activity log carry it
  too. The ceiling is enforced again on the way in: the route validates the
  rate through `speed_ok()` rather than trusting the form.

Findings **stop a mix being filed**; filing one anyway needs a reason and is
recorded against a name. Nothing here refuses a *render*: a check that refuses
the correct thing is a check somebody switches off, and switching this one off
would cost the call-to-action check with it.

## Sending it to the Suite

What the **client approved** goes to their opportunity in Smart 1 Suite: the
audio, the script, and who signed it off. One press on the Review step.

This goes through **`hub/suite_opportunity.push_proposal`**, not a webhook of
its own. The Radio Ad Creator posts its own payload to
`GHL_OPPORTUNITY_WEBHOOK_URL`, and that is deliberately not what this copies —
`modules/commercial_builder/routes/suite.py` faced the same choice and wrote
the answer down: `hub/ghl_contacts.py` is "one token, one location id and one
contact write path for the whole Hub", and a second raw webhook would be a
third answer to *how do we reach GoHighLevel*. A third tool posting its own
would be the fourth. The shared function also finds the contact and refuses to
invent one, so "no Suite contact for this client yet" is a thing a rep fixes
from the same panel rather than a failure.

Four things are held back, each named on the panel rather than left to be
discovered:

* **A spot the client has not approved.** The share page's Approve is the
  gate; what goes to the CRM is what the client signed off, not what we thought
  was ready.
* **A bed chosen with no saved mix.** The review page falls back to the raw
  read on purpose, so a client has something to hear while a bed is composed.
  Delivery must not: sending the naked read as the final file delivers a
  commercial nobody made.
* **A read the script has moved past.** `audio_stale` means the words changed
  after the recording.
* **Audio with no address a salesperson could open.** A Cloudinary render is
  already absolute; a local one is absolutized against `PUBLIC_BASE_URL`, and
  with that unset the spot is held rather than filed with a URL that resolves
  to nothing.

A **spec spot is refused** — an opportunity for a business that has not asked
for one pollutes the pipeline. Pressing again **revises** the same opportunity
rather than opening a second, and a refusal is **recorded as a refusal**:
"nobody has pushed this", "Suite refused it" and "Suite has it" are three
states, and collapsing the middle one makes the button read as never pressed.

## Config

```
OPENAI_API_KEY, OPENAI_MODEL        # reused; without them you get labelled templates
ELEVENLABS_API_KEY                  # voice; scripts work fine without it
ELEVENLABS_MODEL=eleven_multilingual_v2
CLOUDINARY_URL                      # reused; falls back to the persistent disk
FAN_RADIO_FOLDER=smart1-fan-radio
FAN_RADIO_NOTIFY_URL                # optional ping on approve/comment
PUBLIC_BASE_URL                     # required for the share link to be a full URL
```

No new Python dependencies. Audio uploads as Cloudinary `resource_type
"video"` (its type for audio) — uploading it as `image` is what made the
Suite's PDF links 403.

## Install

1. Drop `modules/fan_radio/` into `smarthub/modules/`.
2. Apply `hub-integration.diff` (or make the four edits by hand — see
   `INSTALL.md`).
3. Set the env vars above on Render.

## Files

| File | Lines | What |
|---|---|---|
| `app.py` | ~1640 | Routes: builder API, beds, the mix, the checks, the public approval page |
| `qc.py` | ~190 | This tool's reading of its own row over the shared script panel, plus the trademark and post-game rows |
| `phrases.py` | ~240 | The trademark guard and the safe phrase bank |
| `catalog.py` | ~180 | Dayparts, tones, outcomes; the lengths read from hub/radio_spec |
| `ai.py` | ~340 | Brief reading, spot writing, tighten, casting profile |
| `voices.py` | ~270 | ElevenLabs transport and render; casting is hub/voice_casting |
| `speech.py` | ~120 | Written copy → spoken copy |
| `store.py` | ~380 | Projects, versions, share tokens, feedback, audio assets |
| `suite.py` | ~230 | What may be filed in Smart 1 Suite, and the note on the opportunity |
| `script_presets.py` | 1 | Re-export of `hub/radio_presets.py`, the shared reusable-read library |
| `templates/index.html` | ~1240 | The builder |
| `templates/share.html` | ~340 | What the client sees |
| `templates/library.html` | ~90 | Every project, who approved what |

What is **not** here is as much of the point: the lengths and their
budgets, the bed vocabulary, the mix levels, the length arithmetic, the read
pace and the mix checks are `hub/radio_spec.py`; the named script panel is
`hub/radio_script_qc.py` and the content rules inside it are
`hub/script_contents.py` and `hub/social_plan.py`; the casting question and its
scoring are `hub/voice_casting.py`; the client link is `hub/radio_share.py`;
the reusable-read library is `hub/radio_presets.py`.
Each was a local copy here once, or existed only in the Radio Ad Creator, and
the next fix to any of them now lands once.

## What is still only in the Radio Ad Creator

Written down rather than left to be discovered as gaps. Every one of these is
now a decision; nothing on this list is open.

* **Voice cloning** makes a voice out of somebody's recordings, which is a
  consent question, and one place to answer it is the right number.
* **The companion display banner** is a picture, and this is a radio tool.
* **Variations** clone one spot and patch the offer, which is how a
  single-slot tool writes a second version. A Fan Radio project already holds
  every daypart and length as its own spot, so the capability is the data
  model here rather than a button.
* **The staff *approve spot* press.** Fan Radio files what the **client**
  approved on the share page instead, which is the stronger record of the two
  — it carries their name and the time. A staff gate in front of it would be
  a second, weaker one.
* **The raw `GHL_OPPORTUNITY_WEBHOOK_URL` post.** Fan Radio reaches the Suite
  through `hub/suite_opportunity.push_proposal`, the Hub's one contact write
  path — see *Sending it to the Suite* above.
