# Connecting the smart1marketing.com gameplan pages to the Hub

**Audience:** whoever edits pages on smart1marketing.com.
**What you do:** one iframe per page. No script required.

---

## 1. What is actually wrong

Each gameplan page frames a form. Several of them frame **the wrong service**.

The Hub runs the tool behind every one of these pages — `/land/boat`,
`/land/ski`, `/land/stadium` and six more. Each asks a prospect a few
questions, builds the plan, renders the PDF, and writes the answer through
`hub/leads.py`, which creates the contact in Smart 1 Suite over the Contacts
API and returns an id. That is the path the Leads panel at `/sales/leads`
reports on, and the path that files the plan against a client's 360 record.

The boat page's iframe pointed at `https://smart1boat.onrender.com/?embed=1`
— a **separate Render deployment of that same tool**. Its leads land in its
own storage, so they never reach the Leads panel, and whether they reach Suite
at all depends on that service's environment variables. Stadium's page code
names `blueprint-2.onrender.com` for the same reason.

So this is not "the marketing site has its own form". It is the right tool,
served by the wrong copy of it. Which is why the fix is a URL.

## 2. The URLs

Every one of these was checked against the Hub's route table rather than typed
from memory: each answers **200**, needs **no login**, and is framable from
smart1marketing.com.

| Page | Frame this URL | Leads tagged |
|---|---|---|
| `/football-audio-video-playbook` | `https://smart1-hub.onrender.com/land/stadium/embed` | `stadium` |
| `/boat-dealer-marketing-gameplan` | `https://smart1-hub.onrender.com/land/boat/embed?embed=1` | `boat` |
| `/rv-dealer-marketing-gameplan` | `https://smart1-hub.onrender.com/land/rv/embed` | `rv` |
| `/legal-industry-marketing-gameplan` | `https://smart1-hub.onrender.com/land/legal/embed` | `legal` |
| `/restaurant-weather-marketing-gameplan` | `https://smart1-hub.onrender.com/land/restaurant/embed?embed=1` | `restaurant` |
| `/smart-tourism-ads` | `https://smart1-hub.onrender.com/land/tourism/embed` | `tourism` |
| `/ski-resort-markeitng-gameplan` | `https://smart1-hub.onrender.com/land/ski/embed` | `ski` |
| `/recruitment-digital-marketing-gameplan` | `https://smart1-hub.onrender.com/land/recruit/embed` | `recruit` |
| *(no page yet)* | `https://smart1-hub.onrender.com/land/hvac/embed` | `hvac` |
| `/ims` — **check this one**, see §5 | `https://smart1-hub.onrender.com/tools/calculators/embed/trade` | `calculators` |

**Why boat and restaurant carry `?embed=1` and the others do not.** Those two
tools have their own switch that hides their hero, so the host page does not
show two headlines. The switch reads the `?embed=1` query, and newer Hub builds
also treat the `/embed` path as embed mode on its own — carrying the query as
well makes the URL right against whichever build is deployed when you paste it.
The other seven have no such switch and draw their own hero inside the frame,
so give them a page section that is otherwise bare, or link to them instead.

## 3. What to paste

A plain iframe. **No `<script>` — Simvoly code blocks are not a safe place for
one**, and nothing here needs it:

    <iframe src="https://smart1-hub.onrender.com/land/ski/embed"
            title="Ski Resort Marketing Gameplan"
            style="display:block;width:100%;height:1400px;border:0"
            loading="lazy"></iframe>

Delete the page's existing form or old iframe when you paste. Two forms on one
page means half the leads go nowhere and the split is invisible.

**The height is fixed, and the frame scrolls inside itself.** That is not a
compromise to apologise for: it is what makes each tool's own "jump back to the
top when the report renders" work, because there is a scrollbar for it to move.
Pick a height that fits the finished plan and check it on a phone.

**No trailing slash on `/embed`.** The tourism wizard calls its API with a
relative path, which resolves against the *directory* of the current URL —
`/embed/` would be a 404 the prospect does not meet until they have filled in
the whole form and pressed submit. `/embed/` redirects, so a slash typed by
hand still works, but do not paste one.

### If you can add page-level Header Code

Then the frame can grow to fit instead, with a branded loader over the Render
wake-up. `docs/embeds/boat-page-header-code.html` is that upgrade for the boat
page; it checks that its target exists before touching anything, so it is inert
on any other page. The Hub also serves a generic version of the same idea at
`…/land/<tool>/embed.js` — a one-line loader that writes the iframe itself —
for anywhere a script tag *is* allowed.

## 4. The boat page, done

`docs/embeds/boat-dealer-marketing-gameplan.html` is that page's whole code
block, rewritten: the iframe repointed at the Hub, and the page's own
explanation of the program kept intact — the problem section, the three steps,
the six things the program includes, the report preview, the FAQ and the CTAs.

Four things in it were not code, and are fixed there rather than here:

- Two **editor notes were live on the page**, addressed to prospects
  ("Placeholder metrics — replace with your real campaign results before
  publishing", and "Replace these with real dealer testimonials").
- The **KPI figures were invented** — 3×, 40%, 100%. Replaced with four claims
  the page can stand behind.
- The **two testimonials were fabricated**, attributed to named people. Removed
  rather than rewritten; the markup is left in a comment for real ones.
- The block **opened its own `<!doctype html>`** and styled bare `body`, `h1`
  and `*`, which restyles the site around it. Now scoped under `#s1boat`.

Its header comment also lists three fixes that belong in Simvoly's page
settings: the empty meta description, the broken `og:image`, and a ~200-line
audio-calculator script in the page header that does nothing on that page.

## 5. `/ims` — not yet verified

The Hub has an **IMS Advertising Trade Calculator** (`hub` slug `trade`), live
and public at `/tools/calculators/c/trade` and framable at
`/tools/calculators/embed/trade`. Its leads go through `hub/leads.py` like
every other tool here.

Whether `smart1marketing.com/ims` is currently pointed at it has **not been
checked** — this repository's sandbox cannot reach smart1marketing.com. Paste
that page's code block and it can be answered properly.

One thing is already known, because it appears on the *boat* page: a
**Smart 1 Digital Audio Calculator** script sits in that page's header code.
It is not the IMS trade calculator — it estimates digital audio impressions and
reach from a budget and a CPM — and wherever it runs it delivers no lead
anywhere, because its own webhook POST is commented out and the only other
thing it does with the captured contact is fire a browser event nothing
listens for. If a copy of it is running on `/ims`, that page's leads are being
collected and dropped.

## 6. Two settings, one of which you will not need

**Which domains may frame the tools.** `smart1marketing.com` and its
subdomains, and nothing else — an allowlist rather than the `frame-ancestors *`
the scans widget sends, because that one is pasted onto clients' own domains
and cannot know them in advance. To add a domain, set `HUB_FRAME_ANCESTORS` in
Render (space separated, CSP syntax; it replaces the default rather than adding
to it).

Not to be confused with `HUB_EMBED_FRAME_ANCESTORS`, which is a different
question with a different right answer: that one is about HighLevel framing Hub
pages inside Smart 1 Suite.

**Moving the Hub to another host.** The host appears once per page, in the
iframe's `src`.

## 7. Two things to fix on the site while you are in there

Neither is a guess — both are visible in the list of URLs:

- `/smart-tourism-ads` has the page title **"Accounting Partner Program"**. It
  is the tourism gameplan page, so either the title belongs to another page or
  the content does.
- `/ski-resort-markeitng-gameplan` misspells *marketing* in the URL. Renaming
  it needs a 301 for every existing link and ad destination; the iframe is
  unaffected either way.

## 8. If you would rather keep a native site form

Then the missing piece is CORS, not an embed: `/api/leads/capture` accepts a
public POST but sends no `Access-Control-Allow-Origin`, so a browser on
smart1marketing.com is refused before the request is made. That is a small
change to reverse — but it buys back the problem this replaces, because the
site's form and the Hub's form then have to be kept in step by hand, and the
day they drift nothing says so.
