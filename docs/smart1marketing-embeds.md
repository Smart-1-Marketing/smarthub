# Connecting the smart1marketing.com gameplan pages to the Hub

**Audience:** whoever edits pages on smart1marketing.com.
**What you do:** paste one line per page. Nothing else is configured.

---

## 1. What was wrong

There are two forms per industry, and only one of them is connected.

The Hub runs a working tool for every gameplan page — `/land/boat`,
`/land/ski`, `/land/stadium` and six more. Each asks a prospect a few
questions, builds the plan, renders the PDF, and writes the answer through
`hub/leads.py`, which creates the contact in Smart 1 Suite over the Contacts
API and returns an id. That is the path the Leads panel reports on.

The marketing site carries its own form on each of those pages. Whatever that
form does, it does not do this: nothing in this repository is reachable from
it, `/api/leads/capture` sends no CORS headers so a browser on
smart1marketing.com cannot post to it, and no page URL on the marketing site
appears anywhere in the code. A prospect who fills in the marketing-site form
does not appear in the Leads panel, is not created in Suite, and gets no plan.

Nothing errors, on either side. That is what makes it expensive.

**One earlier attempt is still in the repo and is worth knowing about**, because
it is the shape this replaces. `modules/rv/public/smart1-multipart-embed.html`
was a second copy of the RV form meant to be pasted into the Suite page and to
post back here. It shipped with
`const API_BASE = 'https://YOUR-RENDER-APP.onrender.com'` — a placeholder every
glance reads as configured — and called
`fetch(API_BASE + '/api/rv-demand/estimate-and-submit')`, which omits the
`/land/rv` mount the app actually answers on and so is a 404 even with the host
filled in. It has been replaced with the snippet below.

## 2. What replaces it

Not a copy of the form. The real one, in a frame.

    <script src="https://smart1-hub.onrender.com/land/boat/embed.js"></script>

That writes the iframe where the script tag sits and keeps it the right height.
Because it is the real tool:

- the fields are whatever the tool asks for today, so nothing drifts;
- the mount prefix is decided by the server, so there is no URL to get wrong;
- the lead travels the identical path a lead from `/land/boat` travels,
  because it **is** one.

## 3. The lines to paste

One per page. Paste into a Custom HTML / Code block placed where the form
should appear, and **delete the page's existing form** — two forms on one page
means half the leads go nowhere and the split is invisible.

| Page on smart1marketing.com | Paste this | Leads arrive tagged |
|---|---|---|
| `/football-audio-video-playbook` | `<script src="https://smart1-hub.onrender.com/land/stadium/embed.js"></script>` | `stadium` |
| `/boat-dealer-marketing-gameplan` | `<script src="https://smart1-hub.onrender.com/land/boat/embed.js"></script>` | `boat` |
| `/rv-dealer-marketing-gameplan` | `<script src="https://smart1-hub.onrender.com/land/rv/embed.js"></script>` | `rv` |
| `/legal-industry-marketing-gameplan` | `<script src="https://smart1-hub.onrender.com/land/legal/embed.js"></script>` | `legal` |
| `/restaurant-weather-marketing-gameplan` | `<script src="https://smart1-hub.onrender.com/land/restaurant/embed.js"></script>` | `restaurant` |
| `/smart-tourism-ads` | `<script src="https://smart1-hub.onrender.com/land/tourism/embed.js"></script>` | `tourism` |
| `/ski-resort-markeitng-gameplan` | `<script src="https://smart1-hub.onrender.com/land/ski/embed.js"></script>` | `ski` |
| `/recruitment-digital-marketing-gameplan` | `<script src="https://smart1-hub.onrender.com/land/recruit/embed.js"></script>` | `recruit` |
| *(no page yet)* | `<script src="https://smart1-hub.onrender.com/land/hvac/embed.js"></script>` | `hvac` |

HVAC has a tool and no page. It is listed because leaving one out of a set of
nine is how the ninth is still missing a year later.

### If the builder strips `<script>`

Some page builders do. Use a plain iframe instead — no auto-resize, so pick a
height that fits the finished plan and check it on a phone:

    <iframe src="https://smart1-hub.onrender.com/land/boat/embed"
            title="Boat Dealer Marketing Gameplan"
            style="display:block;width:100%;height:1500px;border:0"
            loading="lazy"></iframe>

**No trailing slash on `/embed`.** The tourism wizard calls its API with a
relative path, which resolves against the *directory* of the current URL: from
`/embed` that is right and from `/embed/` it is a 404 the prospect does not
meet until they have filled in the whole form and pressed submit. `/embed/`
redirects, so a slash typed by hand still works — but do not paste one.

### Setting the starting height

`data-height` is only the height used before the frame reports its own, so it
controls one thing: whether the page visibly jumps on a slow connection.

    <script src="https://smart1-hub.onrender.com/land/boat/embed.js"
            data-height="1500"></script>

## 4. What the page will look like

The framed page is the Hub tool's own page, including its hero. Put the embed
on a section that is otherwise empty, or the visitor reads two headlines.

The Stadium page (`/land/stadium`) is a full marketing page rather than a bare
wizard — hero, navigation and all. On a marketing-site page that already has
its own hero, link to it instead of framing it:

    https://smart1-hub.onrender.com/land/stadium

## 5. Where the leads go

Straight into the Leads panel (`/sales/leads`), with the source column set to
the tag in the table above and the plan PDF attached, and on into Smart 1 Suite
as a contact through `hub/ghl_contacts.py`. The Hub stores the lead **before**
it forwards, so a Suite outage delays a contact rather than destroying a lead;
anything undelivered stays queued and visible and can be retried.

The plan is also filed against the client's 360 record.

## 6. Two settings, and one of them you will not need

**Which domains may frame the tools.** `smart1marketing.com` and its
subdomains, and nothing else. This is an allowlist rather than the
`frame-ancestors *` the scans widget sends, because that one is pasted onto
clients' own domains and cannot know them in advance while these are only ever
on ours. To add a domain, set `HUB_FRAME_ANCESTORS` in Render — space
separated, CSP syntax, and it replaces the default rather than adding to it:

    HUB_FRAME_ANCESTORS='self' https://smart1marketing.com https://*.smart1marketing.com https://newdomain.com

**Moving the Hub to another host.** The Hub's URL appears exactly once per page,
in the script's `src`; the frame URL and the origin check are derived from it.

## 7. Two things to fix on the site while you are in there

Neither is code, and neither is guessed — both are visible in the list of URLs:

- `/smart-tourism-ads` has the page title **"Accounting Partner Program"**. It is
  the tourism gameplan page, so either the title belongs to another page or the
  content does. Check which before embedding.
- `/ski-resort-markeitng-gameplan` misspells *marketing* in the URL itself.
  Renaming it breaks every existing link and ad destination, so if it is
  renamed the old path needs a 301 — and the paste line above needs no change
  either way.

## 8. If you would rather keep the site's own forms

Then the missing piece is CORS, not an embed: `/api/leads/capture` accepts a
public POST but sends no `Access-Control-Allow-Origin`, so a browser on
smart1marketing.com is refused before the request is made. That is a
deliberate default and a small change to reverse — but it buys back the
problem this replaces, because the site's form and the Hub's form then have to
be kept in step by hand, and the day they drift nothing says so.
