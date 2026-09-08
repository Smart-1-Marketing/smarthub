# Fan Radio

Football-themed radio spots in three dayparts — **Pre-Game Prep, Game Day,
Post-Game** — at every length the Radio Ad Creator sells, with a music bed
under them and a customer-facing page where the client listens, approves, or
asks for changes.

Mounted at `/tools/fan-radio`. It carries the same tools as the Radio Ad
Creator because it **reads the same modules**, rather than being built to
resemble them: `hub/radio_spec.py` for the lengths, the budgets, the bed
vocabulary, the mix levels and the checks; `hub/voice_casting.py` for the
casting question and the scoring; `hub/radio_share.py` for the client link.

That is a fix rather than a tidy-up. Every one of those used to be a local
copy here, and three of them had drifted — the versions of this file above
said "same word budgets" and "same casting" while a :15 was 30–38 words here
against 35–42 there, and the casting question was missing two of the five
answers the shared picker offers. Each screen was internally consistent, so
the only way to notice was to open both.

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

Findings **stop a mix being filed**; filing one anyway needs a reason and is
recorded against a name. Nothing here refuses a *render*: a check that refuses
the correct thing is a check somebody switches off, and switching this one off
would cost the call-to-action check with it.

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
| `app.py` | ~1410 | Routes: builder API, beds, the mix, the checks, the public approval page |
| `phrases.py` | ~240 | The trademark guard and the safe phrase bank |
| `catalog.py` | ~180 | Dayparts, tones, outcomes; the lengths read from hub/radio_spec |
| `ai.py` | ~340 | Brief reading, spot writing, tighten, casting profile |
| `voices.py` | ~270 | ElevenLabs transport and render; casting is hub/voice_casting |
| `speech.py` | ~120 | Written copy → spoken copy |
| `store.py` | ~380 | Projects, versions, share tokens, feedback, audio assets |
| `templates/index.html` | ~1240 | The builder |
| `templates/share.html` | ~340 | What the client sees |
| `templates/library.html` | ~90 | Every project, who approved what |

What is **not** here is as much of the point: the lengths and their
budgets, the bed vocabulary, the mix levels, the length arithmetic and the
checks are `hub/radio_spec.py`; the casting question and its scoring are
`hub/voice_casting.py`; the client link is `hub/radio_share.py`. Each was a
local copy here once, and the next fix to any of them now lands once.
