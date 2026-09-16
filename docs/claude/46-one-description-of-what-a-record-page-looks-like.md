## One description of what a record page looks like

`hub/static/hub-detail.css`. The SEO client page is the shape every record-like
screen in this Hub should have — a crumb and a title with the actions beside
them, white cards with a small navy heading and a control on the right,
key/value rows that line up, muted secondary text, one blue button — and it was
written as ninety lines of `.seoc-*` rules inside that one template. So the
three module screens beside it each grew their own idea of what a card is:
Sites Admin with a dark "Smart 1 Sites Admin" bar of its own, the Suite panel
with a second one, and the client lookup at `/clients` still in the old
near-black and lime green. Four screens of one product, three palettes, and a
person moving between them reading it as three different tools.

The primitives are in one stylesheet now, declared under **both** the `s1d-`
names the modules use **and** the `seoc-` ones the SEO page already had, *in
the same rule*. That is the whole point: a change to what a card looks like
lands once, and the page the look came from cannot drift away from the pages
that adopted it. It is loaded twice because the Hub is two apps —
`hub/templates/base.html` links it for the hub's own pages and `wsgi.py`'s
HubBar injects it beside `theme.css` for every mounted module — so a module
that adopts the class names needs no stylesheet of its own. `.s1d-card` carries
its own background and border rather than assuming hub.css's `.card`
underneath it, because a mounted module never loads hub.css.

What each module keeps is what is genuinely its own: Sites keeps the filter
row, the website blocks and the pager; the Suite panel keeps the fact that a
button there may hold a spinner. **A second branded header bar is not one of
them** — the Hub's sidebar is already on the page, so that bar was chrome
twice, and it is what made each of these read as a separate product. What those
modules do still need is a *second level* of navigation (Accounts / Inventory /
Packages; Create / Manage / Activity / Status), which is `.s1d-subnav`. Sites
marks the current section from `request.endpoint` rather than having every view
pass one in — a nav that has to be told which entry to highlight is a nav that
gets it wrong on the next page somebody adds — and the shared strip answers to
`.active` as well as `.on`, because the Suite panel's tabs are driven by a
script that has written `active` since they were an underline bar. Renaming
that in the script to suit a stylesheet would be the stylesheet deciding what
the page's state is called.

**A status pill says the same thing everywhere.** `sites_admin.status_class()`
returned `good` / `warn` / `bad` / `muted`, which are not the modifiers the
shared sheet defines, so its pills were a second set that looked nearly like
the Hub's. It returns `ok` / `warn` / `bad` and **`""`** now — and that last
one is the point: a status this app has never seen is not a *bad* status, so it
is grey rather than red, the confident wrong answer this codebase keeps having
to undo.

**A prebuilt bundle can be restyled, and cannot be rebuilt.** `clients_app/` is
a compiled React app: the minified JS and CSS are committed and there is no
source in this repo, so its markup cannot be edited. What it can be given is a
later stylesheet, and `hub/static/clients-theme.css` is injected by
`clients_index()` *after* the bundle's own `<link>` so equal rules win.
Remapping the five variables it declares does most of the work; the rest is
there because the bundle also hardcodes colors in rules carrying no variable at
all, and a half-converted palette is worse than an unconverted one. Two things
it deliberately does not do. `--s1-dark` is that bundle's ink **and** its dark
surfaces — one variable doing two jobs — so the surfaces are named individually
rather than remapped, or the body text would come out as heavy as a heading.
And nothing in it changes layout: this is a color pass over a working tool, not
a rebuild of one. It is scoped to a `body` class even though it is injected on
one page, because `.kpi`, `.badge`, `.tabs` and `.search` are ordinary words and
an unscoped rule for one of them would restyle a module nobody was thinking
about.

**`:not(:has(.main))` was matching modules, and one of them was laid out from
x=0.** The sidebar offsets `<body>` by 224px except where the page already
offsets itself — hub.css lays the Hub's own pages out with
`.main{margin-left:224px}`, and applying both pushed the content 448px right.
The guard was `.main` anywhere in the document, and "main" is one of the most
ordinary class names there is: the client lookup names its content wrapper
`.main`, so **the whole React app got no offset and its first column of tiles
sat behind the sidebar**, on every visit, with nothing erroring and every page
still passing linkcheck and pagecheck. It is `.shell > .main` now — only
`base.html` puts a `.main` directly inside a `.shell`, which is precisely the
layout the rule needs to keep its hands off. Image Creator, which also uses
`.main` and reads `--s1hub-offset` to size a full-height canvas, was reading 0
for the same reason.

`test_detail_ui.py` asserts all of it, including that the SEO page no longer
restates the rules it handed over: a copy left behind is not a broken page, it
is a page that silently stops matching the others the next time one of them is
edited.

**The look reaches the Hub three ways, and a sheet added to two of them
reaches most of it and not the rest.** `hub/templates/base.html` links it for
the Hub's own pages, `wsgi.py`'s HubBar injects it into the twenty
dispatcher-mounted modules, and the hub app's own `after_request` injects it
into the blueprints registered on it — Google Access, the Image Picker, Page
Image Optimizer, Tickets, the Calculators, Video Search and the Commercial
Builder. That is the same three-way split `hub-thinking.js` already names, and
it bit again here: `theme.css` had never been on any of those blueprint pages,
so adopting the shared look on them did nothing at all until the third injector
carried it.

**An opt-in page layer, because the sweep is otherwise hundreds of edits.** A
module puts `s1d-page` on its content wrapper and the ordinary elements inside
it — a bare `<button>`, a `<table>`, an `<input>`, an `<h1>` — take the Hub's
look without a class on each one. Site Scans alone declared **three palettes
across five staff templates** (`--blue` as `#5b8bff`, `#2563eb` and the cyan
`#009ED2`), each restating `button {}`, `table {}` and `.card {}` in almost the
same words; converting that element by element is hundreds of edits and a fresh
chance to miss one, where deleting a stylesheet block and adding one class is a
change you can read.

Opt-in and scoped, both deliberately: `button`, `table` and `.card` are far too
ordinary to style globally in a sheet twenty modules receive, and a module that
has not asked for it is untouched. It **accepts the names pages already use**
rather than requiring a rename — `ghost` and `sec`, `.on` and `.active`, a tile
written as `<b>` and `<span>` as well as `.v` and `.l` — because a stylesheet
that needs the page renamed to suit it does not get adopted. And it **excludes
the Hub's own injected controls by name**: `hub-help.js` renders a help bubble
as a `<button>`, so a bare element rule turned every help dot on every adopting
module into a pill at once, which is how a broad rule goes wrong.

**A label sits above its control, except one that contains it.** That shape is a
tick box and its wording. The exclusion is a `:has()` rather than something for
the page to win back, because the injected `<link>` comes *after* the module's
inline `<style>` — so a same-specificity local rule loses, which is exactly what
makes adoption a matter of deleting rules.

**And one stray `</div>` closes the wrapper, after which none of it applies.**
The page still renders, every link resolves, `pagecheck` passes — and every rule
scoped to `.s1d-page` silently stops at the break. That is what one extra
closing tag did to Smart 1 Ads, and it is not visible in the diff, so
`test_detail_ui.py` walks the rendered HTML and asserts the wrapper still
contains the page.

**What is kept local is kept for a stated reason.** Smart 1 Ads' buttons default
to *secondary* — nearly every one is a row action and `.b-primary` is the one
that commits, so a page of blue buttons would have no primary action. Google
Finder's `.btn` stays because `app.js` builds most of them with a per-platform
background that color-codes the action to the account it acts on. Neither is
taste; both are the module's own meaning, and the shared sheet carries the
shape either way.

**Client-facing templates are untouched throughout.** The scan widget and its
reports, the client's proposal and social pages, the public estimate, the
gated calculators and the Google Access connect flow are served to somebody
with no Hub account, and a staff look is not what they should arrive wearing.
`test_detail_ui.py` asserts both directions.

**Adopting the primitives and adopting the element layer are two decisions,
and the Proposal Builder takes only the first.** `s1d-subnav` and `s1d-tile`
are asked for one at a time by name; `s1d-page` turns on a layer of bare
element rules, and `.s1d-page button` carries three `:not()`s, which makes it
(0,5,1) — above every one of that module's six single-class button names.
Taking the layer there would have drawn `btn-gold`, `btn-line`, `btn-ghost`
and `btn-back` as solid brand blue, so *Back* and *Convert to IO* would have
looked like the same offer, on the wizard where the difference is a signed
insertion order. The element layer is for a page with no vocabulary to lose —
Image Creator's project list, which had four local rules and now has none.
Both directions are asserted, and the layer check reads the **class
attributes** rather than the file's text: the reason the Proposal Builder
declines it is written in a comment in that template, and a check a file's own
explanation of itself can fail is one somebody deletes.

**What the Proposal Builder did have to lose is the second branded bar.** A
sticky navy strip reading SMART 1 SALES BUILDER sat above the Hub's own
sidebar — chrome twice, and what made the tool read as a separate product
standing next to Client 360. Its four views are a real second level of
navigation and survive as the shared strip, `id="topnav"` and the `on` class
kept because `nav()` selects on both; the rep's name survives as a control in
that strip rather than a chip on the bar, because it is the attribution
written onto every proposal built here and "Set your name" has to be legible
as unset. A sub-nav button is *excluded* from the page button rule rather than
out-specified — three `:not()`s make that rule hard to beat, and an exclusion
does not depend on winning a race.

**A gallery tile is not a card.** Image Creator's project list called its
thumbnail `.card`, which is the name the shared layer uses for a record card,
so adopting the layer would have put a record card's padding and border round
a photograph. Renamed `.proj`, and the collision is the ordinary way a shared
element layer bites: the class was correct in isolation and wrong the moment
somebody else meant something by it. The editor itself takes none of this — it
is a full-height canvas workbench with its own toolbar and tool rail, which is
the shape the Hub collapses its sidebar for.

**Three tools shipped the same second branded bar, and the last two are gone
now.** The Commercial Builder's said *Creative Hub · Commercial Builder* and
the IO Builder's said *SMART1 Campaign Builder AI* — both sticky, both
full-width, both above the Hub's own sidebar, which is chrome twice and is
what makes a tool read as a separate product standing next to Client 360. The
IO's also named the tool a third thing: the tile, the sidebar and Client 360
all call it the IO Builder, and the browser tab did not.

What each needed in its place is not the same, which is the point. The
Commercial Builder's Dashboard and Spot Library are a real second level of
navigation and become the shared strip — **marked from the request**, because
a nav that has to be told which entry to highlight gets it wrong on the next
page somebody adds, and a **wizard step marks nothing**, since the step has
its own stepper and lighting up Dashboard on step four says somebody is
somewhere they are not. The IO Builder is one screen with a progress bar under
its chat, so there was nothing to put back at all.

**And the strip goes inside the page container, not above it.** A full-bleed
bar at the top of the viewport is the branded bar again wearing the shared
classes — it sits over the Hub's own breadcrumb and pins the page's primary
action to the edge of the screen rather than to the column it belongs to.

**A button in the strip is a button, not a nav link.** The Commercial Builder
had already paid for this inside its own sheet: `.cb-topnav a` is (0,1,1) and
`.cb-btn-primary` is (0,1,0), so the muted gray won and painted the
*+ New Commercial* label a dull gray-brown on solid blue. Adopting the shared
strip would have done it again and the module could no longer have answered,
because `hub-detail.css` is injected **after** a module's own stylesheet and
wins every tie the module used to win. So the exclusion lives on the strip —
`a:not([class*="btn"])`, matched on the class containing *btn* rather than on
a list of names, for the reason `ghost` and `sec` are both accepted: the name
is the page's.

**The wide tool that never asked for the rail to fold was the one named for
it.** This file has listed the IO's printable documents beside the Display Ad
Builder's bench and the Proposal Builder's wizard since `collapsed_default`
was written, and the other two are covered — one by the `/tools/display-ads`
prefix, one by `data-s1hub-collapse="1"` — while the IO Builder carried
neither. Its two panels want 970px between them before anything wraps, so
224px of a nav nobody reads while they work is what turns that into a
horizontal scroll on an ordinary laptop. It carries the attribute now, and
**not** `data-module`: no walkthrough is registered for it, and offering a
tour that does not exist is the silence Smart 1 Ads shipped on Settings and
Live campaigns.
