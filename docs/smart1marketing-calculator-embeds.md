# Connecting the smart1marketing.com calculator pages to the Hub

**Audience:** whoever edits pages on smart1marketing.com (Simvoly).
**What you do:** one iframe per page. No script required.

Companion to `docs/smart1marketing-embeds.md`, which covers the nine industry
*gameplan* pages. This file covers the five *calculator* pages.

---

## 1. Read this before pasting anything

Until the change that added this file, **there was no correct code to paste.**
Every calculator URL below answered a browser's iframe request with:

    This Hub page is not available inside Smart 1 Suite.

403, in plain text, in front of the prospect. The cause is in
`hub/__init__.py` and is written up in CLAUDE.md: the hub app's embed policy
treated *any* framed request as a Smart 1 Suite embed, and the calculators are
a blueprint on the hub app rather than a dispatcher-mounted module, so none of
the rules that keep `/land/<tool>/embed` working applied to them.

**So check the deploy before you judge the paste.** If a page still shows that
sentence, the iframe is right and the Hub has not shipped yet.

---

## 2. The URLs

Every one of these was checked against the composed app's route table — booted
through `wsgi.application`, requested with `Sec-Fetch-Dest: iframe`, the header
a real browser sends when it frames a page. Each answers **200**, needs **no
login**, carries **no staff chrome**, and is framable from smart1marketing.com.

| Page | Frame this URL | Calculator |
|---|---|---|
| `/ims` | `https://smart1-hub.onrender.com/tools/calculators/embed/trade` | IMS Advertising Trade |
| `/ctv-ott-calculator` | `https://smart1-hub.onrender.com/tools/calculators/embed/ctv` | Connected TV Reach & Budget |
| `/digital-audio-calculator` | `https://smart1-hub.onrender.com/tools/calculators/embed/digital-audio` | Digital Audio Reach & Budget |
| `/dooh-calculator` | `https://smart1-hub.onrender.com/tools/calculators/embed/dooh` | DOOH Reach |
| `/paid-search-calculator` | **nothing to frame — see §5** | *(does not exist)* |
| *(no page yet)* | `https://smart1-hub.onrender.com/tools/calculators/embed/female-18-34` | Female 18–34 Market |

There is no `?embed=1` on any of these. The calculators read the `/embed/`
route itself, and unlike the boat and restaurant gameplan tools they have no
separate hero to suppress — the embed view already drops the page padding and
goes transparent so it sits on the Simvoly section's own background.

**Each calculator draws its own heading and tagline inside the frame.** Give it
a page section that is otherwise bare, or you will have two headlines.

---

## 3. What to paste

A plain iframe. **No `<script>`** — Simvoly code blocks are not a safe place
for one, and nothing here needs it:

```html
<iframe src="https://smart1-hub.onrender.com/tools/calculators/embed/trade"
        title="IMS Advertising Trade Calculator"
        style="display:block;width:100%;height:1500px;border:0"
        loading="lazy"></iframe>
```

Swap the `src` and the `title` per the table above. **Delete the page's
existing form, calculator or old iframe when you paste.** Two calculators on
one page means half the leads go nowhere and the split is invisible.

### Per page, ready to paste

`/ims`:

```html
<iframe src="https://smart1-hub.onrender.com/tools/calculators/embed/trade"
        title="IMS Advertising Trade Calculator"
        style="display:block;width:100%;height:1500px;border:0"
        loading="lazy"></iframe>
```

`/ctv-ott-calculator`:

```html
<iframe src="https://smart1-hub.onrender.com/tools/calculators/embed/ctv"
        title="Connected TV Reach and Budget Calculator"
        style="display:block;width:100%;height:1500px;border:0"
        loading="lazy"></iframe>
```

`/digital-audio-calculator`:

```html
<iframe src="https://smart1-hub.onrender.com/tools/calculators/embed/digital-audio"
        title="Digital Audio Reach and Budget Calculator"
        style="display:block;width:100%;height:1500px;border:0"
        loading="lazy"></iframe>
```

`/dooh-calculator`:

```html
<iframe src="https://smart1-hub.onrender.com/tools/calculators/embed/dooh"
        title="DOOH Reach Calculator"
        style="display:block;width:100%;height:1500px;border:0"
        loading="lazy"></iframe>
```

**The height is fixed and the frame scrolls inside itself.** Pick a height that
fits the finished plan and check it on a phone. 1500px fits the estimate and
the unlocked plan for all four; the trade calculator grows with the number of
services ticked, so give `/ims` room.

**No trailing slash on the URL.** `/embed/trade` is the route;
`/embed/trade/` is a 404.

### If you can add page-level Header Code

The framed page already reports its own height on every change — it posts
`{type:'s1calc:height'}` to the parent. The Hub serves the matching listener at
`https://smart1-hub.onrender.com/tools/calculators/embed.js`. With it, the
frame grows to fit instead of scrolling inside itself:

```html
<script src="https://smart1-hub.onrender.com/tools/calculators/embed.js"></script>
```

Then add `data-s1calc` to the iframe and drop the fixed height:

```html
<iframe data-s1calc
        src="https://smart1-hub.onrender.com/tools/calculators/embed/trade"
        title="IMS Advertising Trade Calculator"
        style="display:block;width:100%;height:900px;border:0"
        loading="lazy"></iframe>
```

The height in the style is the starting height; the script replaces it as soon
as the frame reports. Keep a real value there rather than `0`, or the frame is
invisible for as long as Render takes to wake.

---

## 4. Where the leads go

Every calculator gates its full plan server-side: `/api/<slug>/estimate`
returns headline metrics only and stores the rest under an unguessable token,
and `/api/<slug>/unlock` trades a validated name, email and phone for it.
Nothing withheld reaches the browser before the contact is captured.

The captured contact goes through `hub/leads.py` — the same path as every
gameplan tool — so it appears in the Leads panel at `/sales/leads` and is
created in Smart 1 Suite. Leads are also listed per calculator at
`/tools/calculators/leads`, with a CSV export.

---

## 5. `/paid-search-calculator` — there is nothing to frame

The Hub has five calculators: `digital-audio`, `ctv`, `dooh`, `trade` and
`female-18-34`. **There is no paid-search calculator.** The page is live on the
marketing site and no Hub tool answers it, so whatever is on it today is not
ours and its leads are not reaching the Leads panel.

Three ways out, in the order they cost:

1. **Point it at Smart 1 Ads.** `/tools/ads` already builds a paid-search
   campaign and a client-facing estimate from an intake — a fuller answer than
   a calculator, but it is staff-operated and has no public embed, so this
   means a link and a conversation rather than an iframe.
2. **Build the calculator.** `modules/calculators/catalog.py` holds each one as
   a fields list and a compute function; a paid-search one is a `CPC_NOTE`-style
   sector benchmark, a budget, and clicks/conversions out. Add it to
   `CALCULATORS` and `ORDER` and the `/c/`, `/embed/` and API routes exist for
   it with no other change.
3. **Take the page down**, or repoint it at one of the four that do exist.

Whichever you pick, leaving the page as it is means a form collecting contacts
that nothing in this Hub can see.

`female-18-34` is the mirror image: a working calculator with no page. It is
ready to frame the moment there is one.

---

## 6. Two settings

**Which domains may frame the calculators.** `smart1marketing.com` and its
subdomains, and nothing else — `HUB_FRAME_ANCESTORS` in Render (space
separated, CSP syntax; it replaces the default rather than adding to it). This
is the same variable the gameplan embeds use.

**If you also frame a calculator on a client's Smart 1 Sites page**, that
domain needs adding to `HUB_FRAME_ANCESTORS` or the frame goes blank. Before
this change no CSP was sent at all, so such an embed worked by accident; it now
works by permission.

Not to be confused with `HUB_EMBED_FRAME_ANCESTORS`, which answers a different
question — whether HighLevel may frame Hub pages inside Smart 1 Suite.

**Moving the Hub to another host.** The host appears once per page, in the
iframe's `src`.
