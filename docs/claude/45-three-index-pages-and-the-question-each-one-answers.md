## Three index pages, and the question each one answers

There are three: **Creative** (`/creative`), **Client Tools** (`/tools`) and
**QA Reports** (`/qa`). What decides which one a tool belongs on is the
question somebody has in their head when they go looking:

- **Creative** — *I have to make something.* Grouped by what comes out:
  **Images**, **Videos**, **Audio**. A finished asset a client receives.
- **Client Tools** — *I have to do something.* **Sales**, **Media Tools**,
  **Content**, **Landing Pages**, **Google**, **Access & Setup**.
- **QA Reports** — *what is wrong, and what do we owe?* The audits, the
  matching lists and the chase lists, whether or not they are built as
  table-returning functions.

That last one is where six tools moved from, and the reason is the whole point
of having three pages. Scan All Clients, Match Sites to Clients, Match Google
Accounts, Campaign Assets Needed, Domain Renewals and Web Tickets were filed
under a Client Tools group called "Client Work", which named where the work
came from rather than what the screen is for — and every one of them is a
queue somebody works down. A report nobody thinks to look for is a report
nobody works, which is the same failure `hub/housekeeping.py` exists to undo
one step earlier. They keep their own URLs: the tile moved, the page did not,
so every Client 360 crumb, bookmark and link in this repo still resolves. They
are `extras` in `qa_home()` rather than `REPORTS` entries, because a REPORTS
entry has to be a function returning a table and these are whole tools.

**The tiles carry a headline and nothing else, four across.** Both pages used
to put a paragraph under each of forty-odd tools, which turned the index into
a scroll nobody read to the bottom of — so the tool at the end was as good as
untiled, which is the failure the tile rule exists to prevent. The prose is on
the tool's own page. `.tool-tiles.compact` in `hub/static/hub.css` is that
shape; the three-column tile beside it still carries prose and QA Reports
still uses it, because there the description **is** the finding. Both shapes
exist rather than one being converted into a worse version of the other.

**A tile that moves must leave the page it moved off.** GPT Ads Builder and the
Social Content Planner went to Client Tools (they write copy and a plan, not a
finished asset); the Display Ad Builder came the other way. Left in both
places, each would be two tiles for one tool and only one of them would ever be
updated — the drift that put Stadium to Screen in one copy of the landing-page
list and not the other. `test_menu_layout.py` asserts every named tool is tiled
exactly where it now belongs *and* absent from where it was, and
`test_gpt_ads.py` and `test_stock_search.py` assert their own tiles from the
other end.

**Video Backgrounds is Video Search.** The tool searches the footage library by
what is on screen; "backgrounds" named one thing the clips get used for. The
mount, the help key and the Cloudinary folder called `Video Backgrounds` in
`hub/video_library.py` are all unchanged — renaming the mount breaks every
existing link, renaming the help key orphans the bubble, and the folder is a
folder on the account.

### The same calculator, asked twice

`/tools/calculators/internal/<slug>` is the staff copy of a media calculator:
the identical fields, the identical `catalog.run()`, and the whole plan in one
response. The public copy at `/c/<slug>` withholds the plan until a validated
name, email and phone are captured, which is exactly right in front of a
prospect and is pure friction on our own screen — worse than friction, in fact,
because a rep sizing a buy for a client we have had for eleven years had to
type *some* contact into that form, and whatever they typed landed in the leads
panel reading exactly like a live prospect. **The internal route stores
nothing**: no estimate row, no contact, no webhook to the Suite.

Four rules on it. It is **deliberately not under `/api/`** — that prefix is in
`PUBLIC_PREFIXES` by declaration, so a route added there is a route outside the
login, and this one answers with the plan the public path is built to withhold.
It runs the **same compute function**, never a second copy of the maths, or the
two versions of one calculator would quote a client different numbers depending
on which link they were sent. **Every calculator in the catalogue has an
internal page** even though only four are tiled under Media Tools: a slug
missing from `INTERNAL_ORDER` loses its tile, not its page. And the page
**says on itself** that it is the internal one, because the client-facing copy
is one URL away and looks almost identical.

**And the guard that made it possible closed an older hole.** `modules/calculators`
is a blueprint on the hub app, so `wsgi.py`'s `AuthGuard` — which wraps only
dispatcher-mounted modules — never saw it, and the hub app has no blanket gate:
`/tools/calculators/leads`, a table of captured names, emails and phone
numbers, answered 200 to anyone with the URL. The guard now sits on the
blueprint rather than on each view, the arrangement
`modules/commercial_builder/__init__.py` arrived at for the same reason, and it
exempts `PUBLIC_PREFIXES` — a guard that also refuses the embedded calculator
is a broken grey box on a client's website, which is the failure
`test_calculator_embed.py` was written about.
