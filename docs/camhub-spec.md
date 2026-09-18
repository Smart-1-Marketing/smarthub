# Lake Cam 2.0 — Design & Build Spec

Buckeye Lake Winery · 2026-09-17 · Smart 1 Marketing

> Exported from the Claude Doc "Lake Cam 2.0 — Design & Build Spec" on 2026-09-17. The build that follows it is `modules/camhub/`; `docs/claude/82-camhub-a-conditions-page-with-a-sponsor-system-behind-it.md` records what was built and where it departs from this text.

## The layout

The current page has the right instinct and the wrong hierarchy. The cam is small, the weather widget is in Celsius, the sponsor row is placeholder Lorem text below the fold, and a second weather bar in the footer competes with the first. Nothing tells a visitor why to stay, and nothing tells a sponsor why the slot is worth paying for.

The fix is to stop treating this as a page with a webcam on it and start treating it as **the Buckeye Lake conditions dashboard** that happens to be hosted by the winery. That reframing is what makes the SEO work, what makes people return daily, and what makes the sponsorship sellable.

### Top to bottom

**1. Presenting-sponsor cam header.** A slim bar directly above the cam: "Buckeye Lake Cam — presented by \[Sponsor logo\]." One line, one logo, links out. It reads as a credit rather than an ad, which is why it performs — and it is the most valuable pixel on the page because every visitor passes it before the video.

**2. The cam, big.** Full content width, 16:9, nothing stealing from it in a sidebar. Two things ride on the frame: a live/offline status dot and a timestamp. Below it, a one-line caption naming the view — "Looking west across Buckeye Lake from the winery dock" — which is also doing SEO work.

**3. The conditions strip.** One horizontal band of six tiles under the cam, each a single number with a label: air temp, wind and gust, lake level vs. normal pool, water temp, sky/precip, sunset. This is the highest-value non-cam content on the page and the reason people bookmark it. One glance, not a data dump.

**4. "Good day on the water?" verdict.** One derived line, color-coded green/amber/red, with a plain-English reason: *"Good boating day. Light west wind at 7 mph, light chop, storms possible after 4 p.m."* Most shareable element on the page, and it costs nothing but logic over data already fetched.

**5. Presenting-sponsor feature block.** Beneath the verdict, before the forecast: logo, image, headline, two lines of copy, CTA button — roughly 2.5× the footprint of a supporting tile. Positioned here because it catches the eye right after the visitor finishes reading conditions, the moment they are done consuming and available to be sold to.

**6. Seven-day forecast.** Seven columns on desktop, horizontal scroll on mobile, Fahrenheit, with wind on every day and not just temperature. This is a boating page.

**7. Advisories, only when active.** A slim bar for NWS watches and warnings and for algae or bacteria advisories at the public beach 150 m from the winery. It **renders nothing when there is nothing** — no empty "no advisories" box. When it does fire it is the most useful thing on the page.

**8. Four supporting sponsor tiles.** Equal weight, image + name + one line + button, below the conditions content and above the SEO block. Visibly a tier down from the presenting sponsor, which is the point — it protects the premium of the top slot.

**9. SEO content block.** Real prose about Buckeye Lake: the lake itself, boating rules and no-wake zones, marinas and ramps, seasonal notes, and the winery's hours and location. This is what makes the page rankable rather than a bare video embed, which search engines have little reason to index.

**10. Winery conversion block.** Reservations, take-out, tastings, events. The cam brings traffic; this is where it turns into covers.

### Why not the ResortCams pattern

You were right that it is too busy, but its real problem is not density — it is that the monetization and the content occupy the same visual register. The Freedom Boat Club ad and the Lake Norman description look alike, so neither reads as trustworthy. Half the page is a link directory to other webcams, which serves the ResortCams network rather than the visitor.

Worth stealing: the weather sits in the page title area ("Davidson, NC — Currently: 87°F, Clear sky"), which helps both scanning and SEO. Worth rejecting: sponsor content dressed as editorial.

### One thing to cut

The footer weather bar. Two weather widgets on one page split attention, and the footer one repeats the same data in a different unit system.

## Sponsor inventory

Five paid positions, two tiers. The tiering is the product — a flat row of five equal logos is worth far less in total than one premium position plus four supporting ones, because the premium position is the only one you can sell as exclusive.

### Tier 1 — Presenting sponsor (one, exclusive)

What they get:

- Logo in the cam header on every page view, above the video
- The feature block under the conditions verdict: logo, image, headline, 40 words, CTA button
- Named in the page's `<title>` and meta description — *"Buckeye Lake Live Cam, presented by \[Sponsor\]"*
- Named in the social share card, so every share carries them
- Mentioned in the cam's YouTube description and in winery social posts about the cam
- Exclusivity inside their category
- Monthly performance report

Natural buyers, roughly in order of fit: a marina or boat dealer, a boat club, a lakefront realtor, a bank or credit union with a Buckeye Lake branch, a dock builder, a powersports dealer. The realtor angle is worth testing first — a lake-view cam is a lake-real-estate lead generator and they have the budget.

### Tier 2 — Supporting sponsors (four)

Image, business name, one line of copy, CTA button. Rotates position on each page load so no one is permanently last. Monthly report. Natural buyers: restaurants, bait and tackle, self-storage, insurance, HVAC, docks and lifts, a golf course, the local chamber.

### Rate-card thinking

Do not quote a rate card until you have a month of real traffic. Price it off actual pageviews, and until then sell a **charter rate** to the first cohort, explicitly discounted in exchange for going first, with a written note that renewal reprices against measured traffic. That does two things: it gets you inventory sold before you have numbers, and it makes the second-year increase a conversation you have already had.

The shape to aim for once you have data: the presenting slot priced at roughly 3–4× a supporting tile, both quoted monthly on annual or seasonal terms. Seasonality is real here — a lake cam's traffic is concentrated May through September — so consider a season package (May–Sept) alongside the annual, priced so the annual is the better value per month. That pushes sponsors toward the annual and smooths your revenue.

One caution on how you sell it: quote impressions and clicks from your own logs and nothing else. Do not project, do not promise traffic growth in writing, and do not quote a CPM comparison against Facebook or Google — the moment a sponsor can benchmark you against a platform CPM, you are competing on a metric where a small local page loses. Sell attention, locality, and the category exclusivity instead.

### House slots

Build the inventory so any unsold position falls back to a house ad rather than an empty box — winery reservations, wine club, an upcoming event. An unsold slot should still be working, and a page with visible gaps is harder to sell into.

## Data sources for Buckeye Lake

I tested every endpoint below live rather than going off documentation. Two findings matter more than the rest: there is a **real USGS gauge in the lake**, which I did not expect, and there is **no source at all for water temperature**, which I also did not expect.

### The good news — a gauge in the lake

USGS site `395540082291600`, "Buckeye Lake near Watkins Island," mid-lake, about 3.5 miles from the winery. It reports **lake surface elevation every 15 minutes**, and it has been doing so since March 2019 — installed as part of the dam rebuild. Live value when I pulled it: **892.07 ft**.

That number on its own means nothing to a boater. But ODNR publishes Buckeye Lake's standard summer pool as **891.6 ft** and winter pool as **888.6 ft**, so you can display *"Lake level: half a foot above normal pool,"* which is a sentence people understand. That single derived line is the most distinctive thing on the page — no other Buckeye Lake site shows it.

One thing to confirm before publishing it as authoritative: USGS states its datum as NGVD29, and ODNR's drawdown table does not state a datum. The numbers line up, so they are almost certainly the same reference, but it is worth a phone call to the Buckeye Lake State Park office before you label anything "above normal" in public.

The same site also reports precipitation, though only since May 2026, so it has no historical depth yet.

**Important:** build against `api.waterdata.usgs.gov`, not `waterservices.usgs.gov`. USGS has announced the legacy WaterServices API is decommissioned in Q1 2027 with degradation possible from August 2026 — which is now.

### Algae and bacteria advisories — live, and close to home

Ohio's beach advisory system (ODH BeachGuard) has a public, unauthenticated JSON API. Three Buckeye Lake beaches are in it, and **beach 245, "Buckeye Lake — Fairfield," is about 150 metres from the winery.**

It returns advisory state with a numeric severity level, which is ideal for driving a badge color. Real records from this summer: a bacteria advisory on beach 245 from 24 July to 31 July 2026, and a historical harmful-algal-bloom advisory at severity 4 on a neighbouring beach. The feed is live and maintained.

Two cautions. The API is undocumented — recovered by reading the site's own JavaScript — so field names can change without notice; wrap every call so that a failure shows "advisory information unavailable" rather than a false all-clear. And the raw bacteria counts and toxin readings are behind auth, so you get the advisory state only, which is all the page needs anyway.

### Weather alerts — live, but pick the right zone

The lake straddles three counties and **three different NWS zones across two forecast offices**, so the wrong zone silently gives the wrong alerts. When I tested, the winery's own point returned zero active alerts while the mid-lake zone returned an active Flood Watch at the same moment.

Simplest correct approach: query by coordinate (`?point=39.9214,-82.4696`) and let NWS resolve zone membership. To catch anything affecting the whole lake, query all three zones together.

The winery geocodes to 39.9214, -82.4696 — Fairfield County, NWS office ILN, grid 104,81. The nearest real weather observation is Newark-Heath Airport, 12 miles north. Label it honestly as such rather than implying it is on the lake.

### What does not exist

**Water temperature: no source.** I checked four ways. USGS has zero temperature sites anywhere near the lake. The Water Quality Portal has 985 Buckeye Lake temperature records, but they stop in October 2018. The Ohio Lake Management Society's volunteer monitoring went dormant around 2014. NWS has nothing inland. If you want water temp on the page, **you have to install a sensor.** A floating probe off the winery dock is the only path.

**On-lake wind: no source.** Ohio has zero NWS marine zones — those exist only for the Great Lakes — so Special Marine Warnings will never fire for Buckeye Lake and a marine-warning widget would sit dead forever. Wave-height fields exist in the NWS schema for this grid but are unpopulated. The nearest anemometer is 12 miles inland, and over-water wind at the dock will routinely differ from it.

**Dam status: no feed in any format.** The rebuilt dam has no public telemetry. Lake elevation is the only live proxy.

**ODNR has no API at all.** Every RSS and feed path I probed on ohiodnr.gov returned 404. No lake levels, no park alerts, no no-wake declarations, no closures. The one genuinely useful page is the winter-drawdown table, which is scrapable HTML and changes once a year — so scrape it annually or just store 891.6 and 888.6 as config constants.

Emergency no-wake declarations and park closures are issued as press releases and on-water buoys. That is real manual entry, and the backend needs a staff override field for it with a timestamp and an auto-expiry.

### Classification

| Field | Status | Source |
| --- | --- | --- |
| Lake level, and delta vs. normal pool | Live API | USGS `395540082291600`, param 62614, 15-min |
| Rainfall at the lake | Live API | Same site, param 00045 |
| Algae / bacteria advisory | Live API (undocumented) | ODH BeachGuard, beach 245 |
| Swim-season window | Live API | ODH BeachGuard monitorings |
| NWS watches and warnings | Live API | `alerts/active?point=` |
| Forecast: temp, wind, gust, precip %, thunder % | Live API | NWS gridpoint ILN 104,81 |
| Current air temp and wind observation | Live API | KVTA, 12 mi north — label it |
| Sunrise / sunset / moon | Computed | No API needed |
| Normal and winter pool elevations | Scrape annually | ODNR drawdown table |
| Drawdown and refill dates | Scrape annually | Same table |
| Boating rules, HP limits, no-wake, hours | Hard-code, review annually | Ohio Administrative Code 1501:47-3 |
| Dam status | Manual entry | No feed exists |
| No-wake declarations, closures | Manual entry | Staff override field |
| Speed and ski zone boundaries | Manual entry | Park map PDF and buoys only |
| **Water temperature** | **Sensor needed** | **No public source, current or dormant** |
| **On-lake wind and chop** | **Sensor needed** | **No marine zone in Ohio** |

### The sensor question

You can launch a credible page today on five live APIs without any hardware. But the two things a boater most wants — water temperature and actual wind at the lake — are precisely the two you cannot get for free, and they are cheap to solve. A dock-mounted weather station with a water probe is a few hundred dollars of hardware, and it converts the page from "nice regional forecast" into the only place on the internet with real Buckeye Lake conditions. That is also the difference between a sponsorship that renews and one that does not, and it is a legitimate line item to put in front of the presenting sponsor as a naming opportunity: *"Water conditions brought to you by — ."*

## Weather API recommendation

### The comparison

| Provider | Cost | Commercial use | Notes |
| --- | --- | --- | --- |
| **NWS (api.weather.gov)** | Free, no key, no cap | Yes, public domain | Authoritative US source. 59 gridpoint fields including gust and thunder probability. No SLA, occasional outages, no support. Requires a User-Agent header. |
| **Open-Meteo** | Free tier is non-commercial only. Paid from about $10/mo for 1M calls | Paid tiers only | Clean API, fast, no key on the free tier. CC-BY attribution required even when paying. Fixed monthly price, no overage surprises. |
| **OpenWeather** | 1,000 One Call 3.0 calls/day free, then roughly $0.0015/call. Developer plan around $180/mo | Yes | What the current page appears to use. Good icons and UI-ready fields. Per-call overage means a traffic spike costs money. |
| Tomorrow.io | Free tier, then enterprise pricing | Yes | Strong on hyperlocal and marine, but priced for enterprise and overkill here. |
| Apple WeatherKit | 500k calls/mo free with a $99/yr Apple developer account | Yes | Good data, but requires JWT auth and an Apple account, and attribution placement is prescribed. |

### What to build

**Primary: NWS.** It is free, unlimited, authoritative for the US, and it already carries everything the page needs — temperature, wind speed, wind gust, precipitation probability, thunder probability, sky cover, visibility, humidity, plus a graduated gust-risk ladder that is genuinely useful for the boating verdict. For a single-location page in Ohio there is no reason to pay for weather data.

**Fallback: Open-Meteo on the paid Standard tier.** NWS has no uptime guarantee and does go down. About $10 a month buys a second source that takes over automatically, which matters because a conditions page showing dashes is a conditions page a sponsor is not paying for. Budget the attribution line.

**Cache aggressively.** Both providers get called on a schedule by your server, not by the visitor's browser. Weather every 10 minutes, lake level every 15, advisories hourly, forecast hourly. Serve every visitor from cache. This is what keeps you inside any free tier permanently regardless of traffic, and it also means a provider outage degrades to slightly stale data rather than to an empty page. Show the fetch time on the strip so stale data is honest rather than wrong.

**Drop the current OpenWeather widget.** Beyond the cost question, the embedded widget on the page today renders in Celsius, is styled nothing like the site, and cannot feed the boating verdict because you do not control its data. Fetching server-side and rendering your own tiles fixes all three at once — and it means the weather is in your HTML, where search engines can see it, instead of inside a third-party iframe where they cannot.

## SmartHub backend

Building this as a SmartHub module is the right call for this client and the right starting point for the product. You get login, billing, and client accounts for free, and the sponsor reporting can live beside the reporting you already produce. The resale question is about how you draw the module boundary, not about which platform — that is covered further down.

### Data model

**`cam_pages`** — one row per cam page, so the module is multi-cam from day one. Slug, title, location name, latitude/longitude, cam embed URL and type, view caption, the SEO prose block, timezone, and a JSON `data_sources` field naming which feeds this page uses. That last field is what makes the module portable: a different lake means different source IDs, not different code.

**`sponsors`** — the advertiser as a business entity. Name, category (used for exclusivity), contact name, email, phone, notes, and a portal login reference so they can see their own numbers.

**`sponsor_placements`** — the sellable unit, and the table that does the work. Links a sponsor to a cam page and a **position** (`presenting`, `supporting`), with:

- Creative: logo, image, headline, body copy, CTA label, destination URL, alt text
- Flight dates: start, end
- Status: draft, scheduled, live, paused, ended
- Weight, for rotation when more than four supporting sponsors share four slots
- Sort order
- A `is_house` flag for fallback ads

Separating sponsor from placement means a sponsor can hold the presenting slot in summer and a supporting tile in winter, renew without re-entering creative, and run different creative on different cam pages when you have more than one. It also means you can queue next month's creative in advance with a start date, which is the feature that stops Mike from having to remember to swap it.

**`sponsor_events`** — raw impression and click records. Covered in the next section.

**`conditions_cache`** — the last good payload from each data source, with a fetch timestamp and a status. Reads come from here; nothing user-facing ever calls an external API directly.

**`manual_conditions`** — the staff override table for what no API provides: dam notes, no-wake declarations, closures, a water-temp reading if entered by hand before a sensor exists. Every row carries who entered it, when, and an expiry, so a stale override cannot sit on the page for a month. Anything past expiry stops rendering and shows up on an admin dashboard as needing attention.

### Admin screens

1. **Cam pages** — list, edit. Rarely touched after setup.
2. **Sponsors** — list with status, category, current placements, this month's impressions and clicks at a glance.
3. **Placement editor** — the screen that matters. Upload image and logo, type the copy, set the URL, pick the position and dates, save. With a **live preview of the actual page beside the form**, which is the difference between a backend Mike uses and one he calls you about. Character counters on every text field, because copy that overflows the tile is the most common way an ad-swap goes wrong.
4. **Conditions override** — a short form: what is happening, how long it should show, how urgent. Plus a list of any override that has expired or is about to.
5. **Reports** — per sponsor, per month, covered below.
6. **Source health** — a plain list of every data feed with its last successful fetch. This is the screen that tells you the BeachGuard API broke before a sponsor tells you the page looks empty.

### Rotation and scheduling rules

- The presenting slot is one placement at a time. Overlapping presenting flights are a validation error, not a silent overwrite — that exclusivity is what the sponsor paid for.
- Supporting slots fill by weight, shuffled per page load so position is fair over time. Record which position was actually served on each impression, so the report can show it.
- Fewer than four sold means house ads fill the rest, in the same visual treatment.
- A flight that ends reverts its slot to house automatically. Nothing expires into a blank space.
- Category exclusivity is a soft warning at save time rather than a hard block — flag the conflict, let a human decide.

## Impression and click tracking

This is the part that makes the product defensible, and it is also the part most local ad programs get wrong — they count page loads and call them impressions. If you are going to hand a sponsor a number every month, the number has to be one you would be comfortable having audited.

### Count a viewable impression, not a page load

Use an `IntersectionObserver` on each sponsor unit and fire an impression only when **at least 50% of the unit has been visible for at least one continuous second.** That is the IAB's viewable-impression standard for display, and adopting it means your number means something and you can say where it comes from.

This matters most for the four supporting tiles, which sit well down the page. Counting page loads would inflate them by a large multiple and the presenting sponsor — whose logo really is seen by everyone — would look no better than a tile nobody scrolled to. Honest counting is what justifies the price difference between the tiers.

Fire once per unit per pageview. Do not re-count on scroll-back.

### Clicks

Route every sponsor link through a redirect endpoint — `/go/{placement_id}` — that records the click and 302s to the destination. Use `rel="nofollow sponsored"` on the outbound link. Deduplicate double-clicks within a couple of seconds from the same session.

### Filtering

Without filtering, a meaningful share of what you report will be bots, and the first time a sponsor compares your number to their own analytics you will have a credibility problem. At minimum:

- Drop known crawler user-agents before counting
- Require the visibility condition to have actually fired, which most headless crawlers will not satisfy
- Rate-limit per session and per IP hash
- Discard sessions with impossible patterns — no scroll, instant click, dozens of pageviews a minute
- Store a `is_filtered` reason rather than deleting, so you can show a sponsor what was excluded if they ask

### Storage

Write raw events to `sponsor_events`: placement ID, sponsor ID, cam page, event type, timestamp, served position, session hash, device class, referrer host, filtered flag and reason. **Hash or truncate the IP; do not store it raw** — no reason to hold that data, and it creates an obligation you do not want.

Then roll up nightly into `sponsor_daily_stats`: one row per placement per day with viewable impressions, clicks, unique sessions, and click-through rate. Reports read the rollup, never the raw table, so reporting stays instant as the raw table grows. Keep raw events 90 days and rollups forever — that keeps the database small while preserving every number you have ever reported.

### One thing to decide early

Batch the event writes. A `sendBeacon` call per impression per visitor is a lot of requests for a page you hope gets popular. Collect the page's events client-side and send one payload on unload or after a short delay. Cheaper, and it survives a traffic spike without taking the site down.

## Sponsor reporting

Two deliverables: a monthly PDF that lands in their inbox without anyone doing anything, and a login they can check whenever they want. The automated email is the one that drives renewal — a sponsor who receives a report every month on the 1st has been reminded twelve times a year that they are getting something.

### The monthly report

One page, sent on the 1st for the prior month, branded to Smart 1 or to the winery:

- **Headline numbers:** viewable impressions, clicks, click-through rate
- **Month over month:** the same three with an arrow, and the prior month's figures beside them
- **A simple daily chart** of impressions across the month — this is what makes the report feel like a real media report, and it shows the sponsor the weekend and weather spikes, which is a conversation starter about the lake and their business
- **Their creative as it actually ran,** rendered in the report. Sponsors forget what they are running. Showing it prompts them to refresh it, and a refresh is an engagement touchpoint.
- **Flight status:** dates, days elapsed, days remaining, and a renewal note when the end date is inside 60 days
- **A plain-English footnote** on what an impression is: that it requires half the ad visible for a full second, and that bot traffic is excluded. That footnote is doing a lot of work — it preempts the "how do I know this is real" question and it makes your number look more rigorous than whatever the last vendor gave them.

### The self-serve dashboard

Inside SmartHub, scoped so a sponsor sees only their own placements: current numbers, a date-range picker, the daily chart, their live creative, flight dates, and a downloadable CSV. Read-only. Do not let them edit their own creative — approval is a service you provide and a reason they talk to you.

### What to promise and what not to

Promise: impressions, clicks, CTR, and the reporting cadence. Put the viewability definition in the contract.

Do not promise: a traffic floor, a guaranteed number of impressions, leads, or conversions. If a sponsor wants conversion tracking, the honest answer is that you can hand off a tagged URL for their own analytics to pick up, and their system counts what happens after the click. Keep the line between your numbers and theirs bright — measuring what happens on their site is their job, and volunteering to own it creates a dispute you cannot win.

One number worth reporting that most local programs never do: **share of page views where the unit was actually viewable.** For the presenting sponsor that will be near 100%, and for a supporting tile it might be 40%. Publishing that gap is what makes the presenting slot obviously worth more, and it means the pricing defends itself without you having to argue it.

## SEO plan

Your instinct that this could get rejected by the engines is the right thing to worry about, but the risk is not where people usually assume. Nothing here is penalized for having ads on it. The risk is that the page has **nothing indexable on it** — a video iframe, a third-party weather widget, and five images with no text is a page Google has no reason to rank, and if the weather and conditions arrive by client-side fetch after load, a crawler may see an empty shell.

### Why this page can rank

"Buckeye Lake webcam," "Buckeye Lake water level," "Buckeye Lake conditions," "Buckeye Lake weather," "is Buckeye Lake open" — these are real recurring local searches with almost no good answer today. A page that genuinely answers them, hosted on an established local domain, is well positioned. The lake-level number in particular is something no competitor has.

### The rules

**Render server-side.** The conditions strip, the forecast, the verdict, and the sponsor copy all have to be in the initial HTML. This is the single most important item on the list. It is also why fetching the weather yourself beats the embedded widget — text inside a third-party iframe does nothing for you.

**Unique title and meta per cam page.** `Buckeye Lake Live Cam — Water Level, Weather & Boating Conditions`. Include the current temperature in the meta description, regenerated on each render, which mirrors what ResortCams does and is worth copying.

**One H1**, then a real heading structure. Right now the page has a banner image where the H1 should be.

**Schema markup.** `VideoObject` for the cam with `isLiveBroadcast` and a `publication` block — this is what can earn a video result in search. `LocalBusiness` for the winery with address, hours, and phone. `Place` for the lake. `BreadcrumbList`. Validate all of it in Search Console rather than assuming.

**Real prose, 400–600 words**, about the lake and not about the cam. Boating rules and no-wake zones, ramps and marinas, what the lake level means, seasonal drawdown, what is visible in the view. Written for a person, which is also what makes it work for the engines and for the AI answer engines that are increasingly where these questions get asked.

**Image discipline.** Descriptive alt text on every sponsor image, lazy-load everything below the fold, WebP, explicit dimensions so the layout does not shift.

**Timestamps.** A visible "updated at" plus `dateModified`. Freshness is a ranking signal for a conditions page and you genuinely have it.

### What would actually get you in trouble

- **Sponsor links without `rel="sponsored nofollow"`.** This is the one real penalty risk on the page. Paid links that pass PageRank violate Google's link-spam policy, and a link-selling pattern is exactly what a manual action is for. Every sponsor link, every time, including the logo in the cam header.
- **Ads above the content.** A large banner pushing the cam below the fold hits the intrusive-interstitial and heavy-ads-above-the-fold guidance. This is a substantive reason the presenting-sponsor bar beats a top billboard, beyond it looking better.
- **Thin duplicated pages** if you roll this out to several cams with the same boilerplate and only the lake name swapped. Each cam page needs its own genuine prose.
- **An empty page when a feed fails.** If the whole conditions area disappears on an API error, a crawl during an outage sees a thin page. Always render the structure with a stale value and a timestamp instead.
- **Cumulative layout shift** from sponsor images loading without reserved space. Core Web Vitals is a ranking factor and sponsor creative is the most common cause of a bad CLS score.
- **Autoplaying audio.** Mute the cam.

### Worth doing

Submit a video sitemap. Register the page in Search Console as its own property path and watch the query report — within a couple of months it will tell you exactly which conditions people want, which is also your best argument in a sponsor renewal conversation.

## Reselling this as a Smart 1 product

There is a real product here, and it is worth building the Buckeye Lake version in a way that does not have to be rewritten to become one. The discipline is simple: **nothing Buckeye-specific goes in code.**

### What is generic

The entire sponsor system — placements, rotation, house fallback, viewable-impression tracking, click redirects, bot filtering, nightly rollups, the monthly report, the sponsor portal. None of that knows what a lake is. This is the bulk of the build and 100% of it is reusable.

The page shell is generic too: cam embed, presenting-sponsor header, conditions strip, verdict line, forecast, advisory bar, sponsor tiles, SEO block. What changes per customer is which tiles appear and what feeds them.

### What is Buckeye-specific

Only the data adapters and the content. Each source — USGS gauge, BeachGuard beach, NWS gridpoint, the pool-elevation constants — becomes a small adapter registered against the cam page, configured with IDs rather than hard-coded. A ski resort swaps the lake-level adapter for a snow-report adapter and everything else stands.

That adapter boundary is the whole product decision. Get it right once at Buckeye Lake and the second customer is a configuration exercise.

### Where the resale value actually sits

Not in the cam page. Anyone can embed a YouTube stream. The value is that **you hand a local business a working local ad network with real reporting** — the thing a marina or ski resort or beach town cannot build and cannot buy. The cam is the traffic magnet; the sponsor system is the product.

That also suggests the pricing model: a setup fee plus monthly SaaS, and the monthly justified by the reporting rather than by hosting. Optionally a revenue share where you sell the sponsorships for them, which is a different and probably better business than licensing software — and one Smart 1 Marketing is already equipped for.

### Target verticals, roughly in order of fit

1. **Marinas and boat clubs** — same buyer set as Buckeye Lake, same conditions data problem
2. **Ski resorts** — already used to conditions pages and already sell sponsorships; snow report replaces lake level
3. **Beach towns and chambers of commerce** — chamber sells the slots to its own members, which solves your sales problem for you
4. **Lakefront restaurants and wineries** — the Buckeye Lake case, repeatable
5. **Surf shops and dive shops** — conditions data is the whole reason people visit their site
6. **Golf courses** — weather-driven, sponsor-friendly, simplest data needs of any of these

The chamber-of-commerce angle deserves attention. A chamber has a member roster that is already a sponsor list, a reason to want a destination page, and a budget line for tourism promotion. One chamber sale is worth several individual businesses and it comes with the sponsors pre-assembled.

### The honest constraint

ResortCams exists and has scale. Do not try to become a webcam network — that competition is unwinnable and it is not the interesting part. Sell the thing they do not: a page the local business **owns**, on their own domain, earning their own SEO, with sponsors they control and reporting they can hand to an advertiser. That is a different product with a different buyer, and their existence actually validates the market.

## Open questions and build sequence

### Needs a decision before build

1. **The cam stream itself.** The current embed shows "Playback on other websites has been disabled by the video owner" — the page is broken today. Before anything else, confirm whether the YouTube stream's embed permissions can be fixed, or whether the camera should feed a different player. Everything else on this page is decoration if the video does not play.
2. **Water temperature sensor: yes or no.** This is the one item with a hardware cost and the one that most changes how good the page is. It is also sellable as its own sponsorship line.
3. **Is the presenting sponsor already identified?** If there is a likely buyer, the mockup should be built with their category in mind and the pitch should go out before the page is finished, not after.
4. **Confirm the pool-elevation datum** with the Buckeye Lake State Park office before publishing "above normal pool" as fact.
5. **Who swaps the creative** — Mike, winery staff, or Smart 1? The answer changes how much hand-holding the admin needs.
6. **Season or annual sponsorships**, which determines whether the tracking needs to handle mid-flight pauses.

### Build sequence

**Phase 1 — the page.** Fix the cam embed, build the layout server-rendered, wire NWS and the USGS lake level, build the conditions strip and the verdict, write the SEO prose, add schema. Sponsors are hard-coded placeholders. This phase alone is a large improvement on what is live and it is what you show a prospective sponsor.

**Phase 2 — the backend.** Sponsor and placement tables, the admin editor with live preview, house-ad fallback, scheduling. At the end of this phase the page is sellable and Mike can run it.

**Phase 3 — tracking.** Viewability observer, click redirect, bot filtering, nightly rollups. Ship this before the first invoice goes out, so the first month a sponsor pays for is a month you have numbers on.

**Phase 4 — reporting.** Monthly PDF, automated send, sponsor portal.

**Phase 5 — the extras.** Advisory feed, manual override, source-health screen, sensor integration if it is a yes.

**Phase 6 — productize.** Extract the adapters, make the cam page configurable, and stand up a second instance to prove the boundary holds.

One sequencing note worth respecting: do not sell the sponsorships until Phase 3 exists. Selling on promised reporting and then building it under time pressure is how the numbers end up being page loads.

---

## Revision — free APIs only, no manual entry

Everything from here supersedes the paid-fallback and sensor assumptions above. Three decisions have changed the design:

1. **Free APIs only.** Nothing that requires a paid tier. This rules out Open-Meteo entirely — its free tier is licensed for non-commercial use, and a sponsor-funded client page is commercial. It also means dropping OpenWeather, whose free allowance is per-day and whose overage is per-call.
2. **No manual-entry fields.** No staff override, no dam-status note, no hand-typed water temperature. Everything on the page comes from an API or is computed.
3. **Water temperature, on-lake wind and chop are optional fields that collapse.** They stay in the schema because other locations will have them — a coastal client gets them free from NOAA buoys — but with nothing feeding them at Buckeye Lake, those tiles simply do not render. No dashes, no "unavailable", no empty box.

That third point is what turns this from a one-client page into the product, so it is specified properly below.

## Presenting-sponsor animation

Six options are built and running live in the companion mockup — each on a stand-in cam frame, each with a replay button, and with a switch that simulates a visitor who has reduced motion enabled. All six are pure CSS: no library, no layout shift, no JavaScript beyond the demo's own replay control.

|  | Treatment | What it does | Best for |
| --- | --- | --- | --- |
| **A** | Broadcast lower third | Card rises over the bottom-left of the cam two seconds in, holds eight seconds, retracts to a corner logo bug | The premium pitch |
| **B** | Gold wipe reveal | One gold sweep crosses the credit bar on load, logo resolves behind it, then still forever | Never covering the water |
| **C** | Title / sponsor crossfade | One slot alternates between the cam's own title and the sponsor credit on an eight-second cycle | Tight header space |
| **D** | Specular shimmer | A highlight crosses the logo every 20–30 seconds, like light on a nameplate | Making a static logo catch the eye |
| **E** | Rotating tagline | Logo holds, the line beside it steps through up to three messages | Sponsors with more than one thing to say |
| **F** | Ambient drift | Slow scale and pan on the sponsor's photo in the feature block downpage | Any supplied photo that looks flat |

### The recommendation: A plus F

The lower third is the only option where the impression is genuinely unarguable — a large card, unmissable, for eight full seconds at the start of every session — and then it gets out of the way, which is what keeps it feeling like a credit instead of an overlay. That combination is the most sellable thing on the page. F then makes the sponsor's feature block downpage look designed rather than pasted in, which matters because sponsor creative is usually whatever they email you.

**B is the safe fallback** if the winery would rather nothing ever cover the water. It costs some presence and some of the pitch, but it will never produce a complaint.

Two to be careful with. **C** makes the impression hard to count honestly, because a visitor who glances once may only see the non-sponsor state — counting it properly needs a state-aware timer, and that complexity is not worth the header space it saves. **E** is the busiest of the six and the only one with moving text beside moving video; sponsors will ask for it, and if you ship it, cap the copy length hard in the admin because those lines truncate on a phone.

### Make it a per-placement setting

Store the treatment on `sponsor_placements` as an enum — `lower_third`, `wipe`, `crossfade`, `shimmer`, `static` — rather than choosing one globally. Different sponsors want different volume, and at renewal "we can turn your treatment up" is a conversation worth having. `static` is also the correct value for a house ad.

## The free-only API stack

Every source below is free with no paid tier and no per-call billing. I called each one during this session, so the status is observed rather than documented.

### Tier 1 — no account, no key, works right now

**National Weather Service** — `https://api.weather.gov`

- Sign-up: **none.** No account, no key, no registration page.
- Requirement: a `User-Agent` header identifying your app and a contact address, e.g. `SmartHub-CamModule/1.0 (smartadops@gmail.com)`. Calls without it get throttled or blocked.
- Docs: [weather-gov.github.io/api](https://weather-gov.github.io/api/)
- What it gives you: `/points/{lat},{lon}` resolves an address to a forecast office and grid; `/gridpoints/{office}/{x},{y}` returns 59 fields including `temperature`, `windSpeed`, `windGust`, `windDirection`, `probabilityOfPrecipitation`, `probabilityOfThunder`, `skyCover`, `visibility`, `relativeHumidity`, `apparentTemperature`, and a `potentialOf20mphWindGusts` through `60mph` ladder; `/gridpoints/.../forecast` and `/forecast/hourly` give the seven-day and 156-hour views; `/alerts/active?point=` gives watches and warnings; `/stations/{id}/observations/latest` gives the nearest real observation.
- Limits: no published hard cap. It is US government infrastructure with no SLA — it does go down occasionally, which the caching strategy handles.
- Cost: $0, permanently. Public domain data.

**USGS Water Data** — `https://api.waterdata.usgs.gov/ogcapi/v0`

- Sign-up: **none.** I confirmed an unauthenticated call returns HTTP 200.
- Docs: [usgs.gov/tools/usgs-water-data-apis](https://www.usgs.gov/tools/usgs-water-data-apis)
- Collections used: `monitoring-locations` (find gauges inside a bounding box — this is the discovery call), `latest-continuous` (current value), `continuous` (the 15-minute series for a sparkline), `daily`.
- **Build on this, not on `waterservices.usgs.gov`.** The legacy API is decommissioned in Q1 2027 with degradation possible from August 2026.
- Cost: $0.

**US Census Geocoder** — `https://geocoding.geo.census.gov`

- Sign-up: **none.** Confirmed working: the winery's address returns 39.921421, −82.469588.
- This is the front door of the whole builder — an address goes in, coordinates come out, and every other probe keys off those coordinates. Better than a commercial geocoder here because it is free, unlimited, authoritative for US addresses, and has no terms-of-service restriction on storing the result.
- Cost: $0.

**Ohio Dept. of Health BeachGuard** — `https://publicapps.odh.ohio.gov/beachguardpublic/api`

- Sign-up: **none.**
- Endpoints: `/beacheslist` (192 Ohio beaches with coordinates — the discovery call), `/advisorieslist?beachId=`, `/monitoringslist?beachId=`.
- **Undocumented.** Recovered from the site's own JavaScript, not published for third-party use. Treat as fragile: wrap every call, and on failure the advisory bar must disappear rather than show a false all-clear.
- Cost: $0. Ohio-only, so it is a state-specific adapter.

**NOAA NDBC buoys** — `https://www.ndbc.noaa.gov`  |  **NOAA CO-OPS tides** — `https://api.tidesandcurrents.noaa.gov`

- Sign-up: **none** for either. Both confirmed HTTP 200.
- Not useful at Buckeye Lake — no buoy, no tide station — but these are exactly what fills the water-temperature, wave-height and tide fields for a coastal or Great Lakes client. Build the adapters now; they cost little and they are the reason the optional fields exist.
- Cost: $0.

### Tier 2 — free but needs an account

**AirNow (EPA air quality)** — optional, nice for a smoke or haze summer

- Sign-up: request an account at [airnowapi.org/Login](https://www.airnowapi.org/Login), then the key appears on the Web Services page. Docs at [docs.airnowapi.org](https://docs.airnowapi.org/).
- Free. Hourly request caps apply per web service and are not published on the FAQ, so confirm the cap against the specific endpoint before relying on it — with hourly server-side caching you will be far under any of them.
- Verify the AirNow Data Use Guidelines cover a sponsor-supported client page before shipping it. I would treat this as a phase-two nice-to-have, not a launch dependency.

### Computed, no API at all

Sunrise, sunset, civil twilight, day length, moon phase and moon illumination are all deterministic from latitude, longitude and date. Compute them in Python — the NOAA solar position algorithm is a few dozen lines, or use a library. Do not call an API for this: it adds a dependency, a failure mode and a rate limit for arithmetic.

The boating verdict, the lake-level delta against normal pool, chop estimated from wind, and "10 mph after dark" are all derived from data you already hold.

### Deliberately excluded

| Source | Why not |
| --- | --- |
| **Open-Meteo** | Free tier is **non-commercial only.** A sponsor-funded client page is commercial, so the free tier does not apply and the paid tier breaks the free-only rule. |
| **OpenWeather** | Free allowance is 1,000 One Call 3.0 calls/day then billed per call. Also the current widget renders Celsius inside an iframe search engines cannot read. Drop it from the page. |
| **Tomorrow.io / WeatherKit** | Paid, or gated behind a $99/yr Apple developer account. |
| **Ohio EPA GIS** | Connections reset; HAB map portal is robots-disallowed; `ohioalgaeinfo.com` now 404s. BeachGuard is the working source. |

### The redundancy question

With Open-Meteo out, there is no second free commercial weather provider worth adding. Build resilience three other ways instead, all free:

1. **Serve from cache, always.** No visitor request ever triggers an outbound API call. Weather refreshes every 10 minutes, lake level every 15, advisories hourly, forecast hourly — all on cron.
2. **Two NWS paths.** If `/gridpoints` fails, fall back to `/stations/{nearest}/observations/latest` for current conditions. Different endpoint, different failure mode.
3. **Serve stale with an honest timestamp.** "Updated 47 minutes ago" is a functioning page. Blank tiles are not. Only after a source has been failing for several hours does its tile collapse under the optional-field rule.

Net API spend at launch: **zero, ongoing.**

## What you have access to right now

The short answer: **everything the Buckeye Lake page needs is already accessible, with nothing to sign up for.** Four of the five sources need no account at all, and the fifth is optional.

One thing I should be straight about: the Render MCP tools let me list your services but not read their environment variables, so I cannot see which API keys `smart1-hub` already holds. What follows is therefore "what this build requires," not an inventory of your credential store. Since the required set is almost entirely keyless, the two lists are nearly identical anyway.

### Ready today — zero setup

| Source | Auth | Action needed |
| --- | --- | --- |
| NWS weather, forecast, alerts | User-Agent header only | None — set the header string in config |
| USGS lake level and gauge discovery | None | None |
| Census geocoder | None | None |
| ODH BeachGuard advisories | None | None |
| NOAA NDBC buoys | None | None — for future coastal clients |
| NOAA CO-OPS tides | None | None — for future coastal clients |
| Sun, moon, twilight | N/A | Compute in Python |

### Needs a free account

| Source | Where to sign up | Time | Priority |
| --- | --- | --- | --- |
| AirNow air quality | [airnowapi.org/Login](https://www.airnowapi.org/Login) — create account, key appears on Web Services page | \~5 min | Optional, phase two |

### Already in the stack, being removed

**OpenWeather.** The live page's weather widget is OpenWeather-powered, so a key probably exists somewhere in the winery site's config. This build does not use it. If that key is on a paid or pay-as-you-go plan, cancelling it after launch is a small recurring saving; if it is on the free tier, just leave it dormant.

### Needs building, not signing up

These are the real work items — none of them is an access problem:

1. **A User-Agent config value** for NWS, set once per environment.
2. **Six source adapters** — NWS gridpoint, NWS alerts, NWS station observation, USGS gauge, BeachGuard, astronomy. Plus NDBC and CO-OPS adapters written now for resale even though Buckeye Lake will not use them.
3. **The cache layer and cron jobs** — nothing visitor-facing calls an external API.
4. **The discovery probes** for the Cam Builder, which are the same adapters run in "find what exists near this point" mode.
5. **A scraper runner** for sources with no API (see below).
6. **The cam embed fix.** Still the only genuine blocker — the current YouTube stream has embedding disabled by the owner, so the page is broken today regardless of what data sits around it.

### The honest gap

The one thing the free-only rule costs you is a second weather provider. There is no free-for-commercial alternative to NWS in the US worth naming. The caching and dual-endpoint strategy covers ordinary outages, but if NWS were down for a day, the page would show data from that morning with an honest timestamp. For a lake cam that is an acceptable failure; it is worth knowing about rather than discovering it.

## Optional fields that collapse

This is the rule that makes one codebase serve a lake in Ohio, a marina in Florida and a ski resort in Colorado.

**Every conditions tile is optional. A tile with no data does not render.** Not a dash, not "N/A", not a greyed-out placeholder — it is absent from the DOM, and the remaining tiles redistribute to fill the row.

### How it works

The conditions strip is a registry, not a fixed layout. Each tile declares a field key, a source adapter, a formatter and a sort weight. At render time the page asks the cache for each key and builds the strip from what came back:

- **Has a fresh value** → tile renders.
- **No value, or stale past its tolerance** → tile is dropped entirely.
- **Fewer than three tiles survive** → the strip itself does not render, and the page falls back to the verdict line alone. Two lonely tiles look broken; no strip looks intentional.

Use CSS grid with `auto-fit` so four tiles fill the same width six would, rather than leaving gaps where the missing ones were. This is also why the strip must be server-rendered: the crawler sees the four real tiles, not a skeleton that JavaScript later fills.

### At Buckeye Lake

Six tiles are defined; **four render.** Air temperature, wind, lake level and sky all have live sources. Water temperature and on-lake chop have none, so those two tiles are simply not on the page — and a visitor who has never seen the other version will not perceive anything as missing. That is the point.

Sunset renders too, since it is computed, which actually gives five.

### At other locations

| Location type | Tiles that light up that Buckeye Lake's do not |
| --- | --- |
| Coastal marina | Water temp, wave height, wave period, tide state and next tide — all free from NDBC and NOAA CO-OPS |
| Great Lakes | Same, plus real over-water wind from a lake buoy, plus NWS marine zone warnings |
| Ski resort | Snow depth, new snow, base temp, wind hold risk — from SNOTEL and NWS |
| River outfitter | Streamflow in cfs, gauge height, water temp — USGS covers all three on most runnable rivers |
| Golf course | Wind, precip probability, lightning risk, frost delay — NWS alone |

Every one of those is a free source. The tile registry is what lets a new vertical be a config exercise instead of a rebuild.

### The staleness tolerance

Each tile carries its own. Weather tolerates 90 minutes, lake level 2 hours, advisories 24 hours, astronomy never expires. Past tolerance the tile collapses rather than lying. This is the mechanism that replaces the manual-entry override: nothing waits on a human, and nothing wrong stays up.

### What this costs

One thing worth naming: because the layout is now data-dependent, a sponsor's position can shift depending on how many tiles rendered. Keep the sponsor blocks outside the collapsible region — the presenting bar sits above the cam and the feature block below the verdict, so neither ever moves. Reserve the strip's row height with `min-height` so a tile appearing or vanishing on a refresh does not shift the page and hurt the Core Web Vitals score.

## Cam Builder — address in, sources out

This is the piece that turns the module into a product: the same pattern as the Smart Forecast builder. You type an address, it goes and finds what data exists at that location, tells you what it found and what it could not, and provisions the customer.

### Step 1 — the address

One field. Paste `13750 Rosewood Rd NE, Thornville, OH 43076`, plus a business name and a cam embed URL.

The Census geocoder resolves it to coordinates. If it returns no match or several, show the candidates and let the operator pick — a wrong pin silently gives the wrong weather grid, and at Buckeye Lake specifically it would give the wrong NWS alert zone, which is a real failure mode rather than a theoretical one.

Then classify the location. Ask the operator one question — *what is this cam looking at?* — with answers like inland lake, river, coastal, Great Lakes, mountain/ski, golf, town square. That single answer decides which probes run and which tiles are candidates. Do not try to infer it; the operator knows and guessing it wrong wastes probe calls.

### Step 2 — the probe run

Every probe is a free API call, they run concurrently, and each returns *found / not found* with whatever identifier it discovered. For the winery's coordinates:

| Probe | Call | What comes back |
| --- | --- | --- |
| **Weather grid** | `api.weather.gov/points/{lat},{lon}` | Office, grid X/Y, forecast zone, county, timezone, radar station, observation-station list |
| **Nearest observation** | `/gridpoints/{o}/{x},{y}/stations` | Ranked station list — take the first, record its distance |
| **Marine zone** | `/zones?type=marine&area={state}` | Whether marine products exist at all. Zero for Ohio, 21 for Lake Erie |
| **Water gauges** | USGS `monitoring-locations?bbox=` ± 0.12° | Every gauge nearby with its site type — `LK` lake, `ST` stream, `GW` groundwater |
| **Gauge parameters** | USGS `latest-continuous` per candidate | Which parameters each gauge *actually* reports now, not what its metadata claims |
| **Beach advisories** | BeachGuard `/beacheslist` | Nearest monitored beach, if the state has a feed |
| **Buoys** | NDBC `activestations.xml` | Nearest buoy and its distance |
| **Tides** | CO-OPS station metadata | Nearest tide station |
| **Astronomy** | computed | Always available |

The gauge probe is where the real intelligence lives, and it needs two rules. **Filter by distance and site type** — a bounding box around Buckeye Lake returns 45 gauges including groundwater wells and streams 11 miles away; only lake-type sites within a few miles are candidates for a "lake level" tile. And **verify, do not trust metadata** — of the Buckeye Lake sites in USGS, two look real in the site catalogue and return nothing at all. The probe must call `latest-continuous` on each candidate and keep only the ones that answer with a value and a recent timestamp.

### Step 3 — the review screen

The builder proposes; the operator confirms. Three groups:

**Found and confirmed** — each with the value it just retrieved, so the operator can sanity-check it. "Lake level: 892.07 ft, 12 minutes ago, USGS 395540082291600, 3.5 mi" is verifiable at a glance. A checkbox per source, all pre-ticked.

**Found but needs a decision** — where the probe found more than one candidate or something that needs human judgment. At Buckeye Lake: which of the three BeachGuard beaches to watch (it should default to the nearest, which is 150 m away), and the normal-pool elevation, which is not in any API and has to come from the scrape queue.

**Not available here** — listed plainly with the reason, because this list is what the operator shows the client. "Water temperature: no USGS site within 20 miles, no buoy, last recorded reading 2018." "On-lake wind: no marine zone in Ohio; nearest anemometer 12 miles north." Those tiles are switched off and collapse. Being able to say *why* something is absent is worth more to a client conversation than the tile would have been.

### Step 4 — provision

On confirm, the builder writes the `cam_pages` row and its `cam_sources` children, runs a first fetch of everything to populate the cache, registers the cron entries, generates the starter SEO prose from what it learned (lake acreage, county, the gauge's normal pool, the state's boating rules), creates the five empty sponsor placements with house ads in them, and hands back a preview URL.

Target: **under five minutes from address to a working page.** That number is the product.

### Worth building into it

A **re-probe** action. Sources appear and vanish — USGS installed the Buckeye Lake gauge in 2019 and added precipitation to it in May 2026. A quarterly re-probe across every provisioned page, reporting only the deltas, means a client's page gets better without anyone remembering to look. It is also a genuine retention argument at renewal.

## The scrape queue

Some facts a conditions page needs exist on a web page and in no API. The builder's job is to notice them, name them, and queue them — not to leave the operator guessing what is missing.

### What actually needs scraping at Buckeye Lake

Exactly one thing: **the normal and winter pool elevations**, 891.6 ft and 888.6 ft, which live in a table on ODNR's winter-drawdown page along with the drawdown and refill dates. Without them the USGS reading is a meaningless number; with them it becomes "half a foot above normal."

That table changes once a year. So this is not really live scraping — it is an annual refresh with a value cached in between.

### How a scrape target gets handled

1. **The builder proposes it.** When a location type implies a fact that has no API — pool elevation for a managed lake, ramp status, park hours — the builder adds a row to the scrape queue with the field it needs, a candidate URL, and the reason no API covers it.
2. **The operator confirms the URL and the selector.** A small screen: fetch the page, show the extracted candidates, let the operator click the right row or cell. What gets stored is the field key, the URL, the extraction rule, and a refresh cadence.
3. **It runs on cadence, not on request.** Annual for pool elevations, daily at most for anything else. Never in a visitor's request path.
4. **The extracted value is treated as a source like any other,** with a fetch timestamp and a staleness tolerance, so a scrape that breaks collapses its tile the same way a dead API does.
5. **Breakage is reported, not silent.** If an extraction returns nothing or a value outside a sane range — a pool elevation that is suddenly 40 feet different — it holds the last good value and raises it on the source-health screen.

### The rules that keep this out of trouble

- **Robots and terms first.** The builder checks `robots.txt` before proposing a target and refuses disallowed paths. Ohio EPA's HAB map portal is robots-disallowed, which is exactly why it is not a source.
- **Public, factual, low-volume.** One request a year to a state agency page for two published numbers is not a burden on anyone. That is the shape every target should have.
- **Identify yourself** with a real User-Agent and a contact address, the same as for NWS.
- **Never scrape as a substitute for an API that exists.** If USGS has the number, take it from USGS.
- **Never scrape a competitor's cam page.** Obvious, but worth writing down before someone suggests pulling a conditions figure off another regional site.

### A caution carried forward

USGS states its elevation datum as NGVD29; ODNR's table does not state a datum. The numbers are consistent, so they are almost certainly the same reference, but the "above normal pool" label depends on that assumption. One phone call to the Buckeye Lake State Park office settles it. Until then, it is fine to show the raw elevation and hold the delta back — the delta is the better line, so it is worth the call.

### What is deliberately not in the queue

Dam status, emergency no-wake declarations, park closures. There is no feed, and with manual entry off the table these simply do not appear on the page. That is the right trade: a conditions page that is always automatic and slightly less complete beats one that depends on someone at the winery remembering to post a notice.

## How it sits inside SmartHub

Built to match the conventions already in the smarthub repo rather than inventing a new pattern — a module directory, read-only fetch functions, a Client 360 card, and exposure through Ask SmartHub and the MCP gateway.

### Module shape

A `modules/camhub/` directory with one file per concern: an `adapters/` folder holding `nws`, `usgs`, `beachguard`, `ndbc`, `coops`, `astro` and `scrape`; then `builder` (geocode, probe orchestration, provisioning), `tiles` (the registry and the collapse rule), `verdict` (the conditions verdict logic), `sponsors` (placements, rotation, house fallback), `tracking` (viewability ingest, click redirect, bot filter), `reports` (monthly PDF and portal queries), `render` (server-side page render plus JSON-LD) and `cron` (the scheduled refresh entry points).

Every adapter exposes the same two functions: `probe(lat, lon)` for discovery and `fetch(config)` for the scheduled pull. That symmetry is what makes the builder possible — discovery is simply every adapter's probe run concurrently.

### Tables

`cam_pages`, `cam_sources`, `cam_tiles`, `conditions_cache`, `scrape_targets`, `sponsors`, `sponsor_placements`, `sponsor_events`, `sponsor_daily_stats`.

`cam_sources` is the important one: a row per provisioned source holding its adapter name, its config JSON (the gauge ID, the beach ID, the grid coordinates), its cadence, its last-success timestamp and its last error. The source-health screen is a select on that table.

**All of it in Postgres, none of it on the `/var/data` disk.** This lines up with the decision already made to get SmartHub off that disk so deploys stop causing outages — a new module should not reintroduce the dependency. Sponsor images and logos go to object storage, not the disk.

### Cron

Render cron entries rather than an in-process scheduler, so a restart never silently stops the refresh:

- Weather and observations — every 10 minutes
- Lake level and gauges — every 15 minutes
- Advisories — hourly
- Forecast — hourly
- Astronomy — daily at 00:05 local
- Scrape targets — per target, annual for pool elevations
- Nightly rollup of `sponsor_events` into `sponsor_daily_stats` — 02:00
- Monthly sponsor reports — 1st at 06:00
- Quarterly re-probe of every provisioned page — reports deltas only

Every job writes its outcome to `cam_sources`, so "is this page healthy" is one query and not a log dig.

### Client 360 and Ask SmartHub

Following the pattern the optimization modules already use, the cam module contributes a **Client 360 card** for any client with a provisioned page: this month's pageviews, the five placements with their impressions and clicks, source health as a single green/amber/red, and a link into the builder.

And a `get_cam_performance` tool on the MCP gateway with the same period convention as `get_client_performance`, so Ask SmartHub can answer "how did the Buckeye Lake cam do last month" and "which sponsor has the best click-through" without anyone opening a dashboard. That is the single highest-leverage integration here — it makes the sponsor conversation something you can have from your phone.

One note on the executive report: the standing instruction is that client-facing monthly reports spin positive. That is the right posture for the winery's own report. The **sponsor** report is a different document with a different reader — an advertiser checking whether they got what they paid for — and it should stay plainly factual. Mixing the two tones is how a sponsor stops trusting the numbers.

### Where the page is served from

The winery's site is its own service (`buckeye-lake-winery-hotsheet`, on the Ohio region). Two options: SmartHub serves the cam page on a path under the winery's domain via reverse proxy, or SmartHub exposes a JSON endpoint and the winery service renders it. **Prefer the first** — the SEO plan depends on server-rendered HTML with schema on the winery's own domain, and that is easier to guarantee when the module that owns the data also owns the render. The second option puts the rendering logic in a place you would then have to duplicate for every future client.

## The Buckeye Lake Winery build

This is the exact configuration the Cam Builder would produce for this address. Every value here came back from a live call during this session, so it is a config file rather than a proposal.

### Resolved location

|  |  |
| --- | --- |
| Address | 13750 Rosewood Rd NE, Thornville, OH 43076 |
| Coordinates | **39.921421, −82.469588** (Census geocoder) |
| County / township | Fairfield, Walnut Township |
| Timezone | America/New\_York |
| Location type | Inland lake |
| Lake | Buckeye Lake, 3,100 acres, unlimited horsepower |

Worth flagging: the lake straddles three counties and **three NWS zones across two forecast offices.** The winery itself is Fairfield County, office ILN, zone OHZ065. Using the mid-lake zone instead would have given a different answer — during my testing the winery point had zero active alerts while the mid-lake zone had an active Flood Watch. The builder must key alerts off the winery's coordinates, with an option to also watch the two neighbouring zones for lake-wide events.

### Provisioned sources

**1. Weather forecast and current conditions** — adapter `nws`

- Office **ILN** (Wilmington), grid **104, 81**. Radar **KILN**.
- Fields taken: temperature, apparent temperature, wind speed, wind gust, wind direction, sky cover, precipitation probability, thunder probability, relative humidity, visibility.
- Cadence: 10 minutes for current, hourly for the seven-day.

**2. Nearest real observation** — adapter `nws`, station endpoint

- Station **KVTA**, Newark-Heath Airport, 40.023 / −82.463 — **12 miles north.**
- Used as the fallback if the gridpoint call fails, and to sanity-check the forecast's current temperature.
- Label it "Newark-Heath Airport, 12 mi N" wherever it appears. Do not imply it is on the lake.

**3. Weather alerts** — adapter `nws`, alerts endpoint

- Primary: `alerts/active?point=39.9214,-82.4696`.
- Optional lake-wide: zones OHZ065, OHZ056, OHZ066.
- Cadence: hourly, but this is the one source worth polling every 15 minutes in storm season.

**4. Lake level** — adapter `usgs`

- Site **395540082291600**, "Buckeye Lake near Watkins Island," 39.9278 / −82.4878, about 3.5 miles from the winery. Site type `LK`.
- Parameter **62614**, water surface elevation above NGVD 1929, every 15 minutes, record back to March 2019.
- Live value observed: **892.07 ft**, provisional.
- Also available at the same site: parameter **00045**, precipitation, but only since May 2026 — provision it, do not feature it until it has a season of history.
- Displayed as a delta against 891.6 ft normal pool, pending the datum confirmation.

**5. Beach advisories** — adapter `beachguard`

- Beach **245**, "Buckeye Lake — Fairfield," 39.922039 / −82.470741 — **about 150 metres from the winery.** Of the three Buckeye Lake beaches in the feed, this is unambiguously the right one.
- Returns advisory type, a numeric severity, the reason, start date and reopen date. Real record from this summer: a bacteria advisory 24–31 July 2026. A neighbouring beach has a historical algal-bloom advisory at severity 4.
- Also pulls the swim-season window, currently 23 May to 7 September, used to suppress the widget out of season rather than show a stale all-clear.
- Cadence: hourly. Fails closed — no data means no advisory bar.

**6. Astronomy** — computed

- Sunrise, sunset, civil twilight, day length, moon phase. Feeds both the sunset tile and the "10 mph after dark" note in the verdict.

### Scrape queue — one target

**Pool elevations**, from ODNR's winter-drawdown table: normal 891.6 ft, winter 888.6 ft, drawdown 15 Nov–15 Dec, refill 1 Mar–1 May. Annual cadence. This is what converts 892.07 into "half a foot above normal."

### Tiles that render — five of eight

| Tile | Renders | Source |
| --- | --- | --- |
| Air temperature | Yes | NWS gridpoint |
| Wind and gust | Yes | NWS gridpoint — labelled as regional, not on-lake |
| Lake level vs. normal pool | Yes | USGS 395540082291600 + scraped pool constant |
| Sky / storm chance | Yes | NWS gridpoint |
| Sunset | Yes | Computed |
| Water temperature | **Collapses** | No source exists |
| On-lake chop / wave | **Collapses** | No marine zone in Ohio |
| Air quality | Deferred | AirNow, phase two |

Five tiles is a clean row and reads as complete. Nobody will notice the two that are absent.

### Sponsor inventory as provisioned

One `presenting` placement with animation `lower_third`, four `supporting` placements with `static`. All five created empty at provision time with house ads in them — winery reservations, wine club, events, and a "sponsor this tile" unit that doubles as the sales pitch.

### Page and SEO

- Path: `/lake-cam` on the winery's own domain, server-rendered.
- Title: *Buckeye Lake Live Cam — Water Level, Weather & Boating Conditions*, with the current temperature injected into the meta description on each render.
- Schema: `VideoObject` with `isLiveBroadcast`, `LocalBusiness` for the winery, `Place` for the lake, `BreadcrumbList`.
- Starter prose generated from what the builder learned: acreage, counties, canal history, the dam reconstruction, how to read the lake level, the drawdown schedule, and Ohio's boating rules for an unlimited-horsepower state park lake — 10 mph default, faster only in designated zones during daylight, no wake within 300 feet of ramps and docks.
- Every sponsor link carries `rel="nofollow sponsored"`.

### The one blocker

The cam stream. The current embed returns "playback on other websites has been disabled by the video owner," so the page is broken today no matter what data surrounds it. Either the stream's embed permission gets turned on, or the camera feeds a player you control. Nothing else in this plan matters until that is settled.

## Delivery plan

Six sprints. Buckeye Lake is the test case, and every sprint is built so the generic version falls out of it rather than being retrofitted afterwards.

### Sprint 1 — adapters and cache

The six adapters plus NDBC and CO-OPS, each with `probe` and `fetch`. The `conditions_cache` and `cam_sources` tables. Cron entries. A source-health screen.

*Done when:* the cache holds live Buckeye Lake values on a schedule and the health screen shows six green rows. No page yet.

### Sprint 2 — the page

Fix the cam embed first. Then the tile registry with the collapse rule, the verdict logic, the seven-day forecast, the advisory bar, the SEO prose block, schema markup, server-side render.

*Done when:* `/lake-cam` renders five tiles, a verdict, a forecast and no sponsor system — and passes Search Console's rich-results test. This is what you show a prospective presenting sponsor.

### Sprint 3 — sponsors

The sponsor and placement tables, the admin editor with live preview beside the form, the five animation treatments, rotation, house-ad fallback, scheduling with future start dates.

*Done when:* Mike can add a sponsor, upload creative, pick an animation, set a flight, and see it live — without calling you.

### Sprint 4 — tracking

Viewability observer, batched event ingest, click redirect with bot filtering, nightly rollup.

**Do not sell a sponsorship before this ships.** Selling on promised reporting and building it under deadline is how the numbers end up being page loads.

*Done when:* a week of real traffic produces defensible impression and click figures, and the filtered share is visible.

### Sprint 5 — reporting and the hub

Monthly PDF with the daily chart and the sponsor's own creative, automated send on the 1st, the read-only sponsor portal, the Client 360 card, the `get_cam_performance` MCP tool.

*Done when:* a sponsor receives a report nobody assembled by hand, and Ask SmartHub can answer questions about the cam.

### Sprint 6 — productize

The Cam Builder front end: address field, location-type question, concurrent probe run, review screen, provisioning, re-probe. Then prove the boundary by standing up a second page at a different location type — a river or coastal spot where the collapsed tiles light up and the buoy adapters earn their keep.

*Done when:* an address goes in and a working page comes out in under five minutes, with no code written.

### What needs you before Sprint 1

1. **The cam stream.** Can the YouTube embed permission be turned on, or does the camera need a different player? Everything waits on this.
2. **Serving approach** — SmartHub renders the page behind the winery's domain, or the winery service renders from a SmartHub JSON endpoint. I recommend the first; it is a five-minute decision with large SEO consequences.
3. **Animation choice** — A plus F, or B if the winery will not have anything cover the water.
4. **One phone call** to the Buckeye Lake State Park office to confirm the pool-elevation datum, so "above normal pool" can be stated as fact.
5. **Whether a presenting sponsor is already in mind.** If so, Sprint 2's output becomes a pitch rather than a demo, and the copy should be written with that category in view.

### What I would not do yet

Hold AirNow, the air-quality tile, and the second cam page until after Sprint 5. And resist adding sources because they exist — the five-tile strip is the right density for a page whose real job is the video. Every additional tile makes the cam smaller and the sponsor cheaper.
