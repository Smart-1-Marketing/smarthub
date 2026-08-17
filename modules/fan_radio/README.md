# Fan Radio

Football-themed :15 and :30 radio spots in three dayparts — **Pre-Game Prep,
Game Day, Post-Game** — plus a customer-facing page where the client listens,
approves, or asks for changes.

Mounted at `/tools/fan-radio`. Built to the same shape as Radio Promo: same
tone list, same word budgets, same pronunciation pass, same ElevenLabs
casting and measured runtime, so a script can move between the two tools
without re-timing.

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
5. **The voice** — a casting profile from the brief, matched against the
   ElevenLabs pool with the reasons shown. Renders report **measured**
   runtime from `/with-timestamps`, or say "estimated" when they can't.
6. **Send it to the client** — headline, intro, optional CTA button, link on.

## Word budgets

| Length | Words | Why |
|---|---|---|
| `:15` | 30–38 | Same clock as Radio Promo and the Commercial Builder |
| `:30` | 65–75 | Same |

Over-budget scripts are **flagged with the overage and re-tightened, never
truncated** — trimming clips a word off the end of the phone number.

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
| `app.py` | ~600 | Routes: builder API, public approval page, audio |
| `phrases.py` | ~230 | The trademark guard and the safe phrase bank |
| `catalog.py` | ~170 | Dayparts, lengths, budgets, tones, outcomes |
| `ai.py` | ~290 | Brief reading, spot writing, tighten, casting profile |
| `voices.py` | ~230 | ElevenLabs matching and rendering |
| `speech.py` | ~120 | Written copy → spoken copy |
| `store.py` | ~280 | Projects, versions, share tokens, feedback, audio |
| `templates/index.html` | ~560 | The builder |
| `templates/share.html` | ~330 | What the client sees |
| `templates/library.html` | ~110 | Every project, who approved what |
