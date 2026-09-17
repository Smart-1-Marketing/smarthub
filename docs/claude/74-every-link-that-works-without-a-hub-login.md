# Every link that works without a Hub login

Almost everything in this Hub is staff-only behind one session cookie. The
exceptions are the point of several of the tools: a proposal a client accepts,
a spot they approve, a dashboard they open, a widget on their own website. Each
one is a URL that works for somebody who has no account here and never will.

This file is the inventory of those, and the reasoning that goes with adding
one. It exists because **the question "what can a stranger reach?" cannot be
answered by reading any single file.** Four separate mechanisms make a path
public, and three of them are invisible from the other three:

1. **A dispatcher-mounted module** declares `PUBLIC_PREFIXES`, and `wsgi.py`'s
   `_mount()` hands the same tuple to `AuthGuard` (no login) and `HubBar` (no
   staff chrome). One declaration, both halves, which is why it is read from the
   module rather than restated in `wsgi.py`.
2. **A blueprint on the hub app** never passes through `AuthGuard` at all. It
   calls `hub/blueprint_guard.install(bp, public=…)`, and what it names there is
   open. A blueprint that forgets the call entirely serves everything it has.
3. **A hub route** is guarded view by view. There is no blanket gate, so a route
   with no `_require_api()` or equivalent is public by omission rather than by
   decision.
4. **A proxied Node module** decides for itself, inside the proxy, before the
   request leaves Python. Its internal paths are not Flask rules and no sweep of
   the URL map can see them.

## The authoritative source is a test, not this file

`test_blueprint_guards.py` boots the composed application and asks **every**
route anonymously — the static ones, the ~330 parameterized ones, and the write
routes, each against its own allowlist. Every entry carries the reason it is
public, and the file separately asserts that no entry names a route that has
stopped existing and that every entry is genuinely reachable, so an exemption
cannot outlive what it exempts or quietly cover whatever is added at that path
next.

**When this document and that test disagree, the test is right.** It is
measured; this is written down. What this file adds is the shape of the thing —
the categories, the blind spots, and what to do when you add one.

## One-per-client links, minted for a job

A staff action creates a token and the link is sent to one client. This is the
bulk of the client-facing surface.

| Link | What the client does with it |
|---|---|
| `/client-links/<token>` | The index of everything below for one client, minted at `POST /api/client/client-links` |
| `/tools/image-picker/pick/<token>` | Uploads their own photographs |
| `/connect/<token>`, `/done`, `/start` | Google Access consent |
| `/connect/youtube/<token>`, `/start`, `/review` | YouTube owner consent and draft review |
| `/tools/lsa/intake/<pid>/<token>` | LSA business-information form; expiring and revocable |
| `/sales/builder/p/<token>`, `.pdf`, `/accept`, `/opened` | Reads and accepts a proposal |
| `/proposal-execution/needs/<token>` | Answers what we need to launch |
| `/tools/ads/estimate/<token>`, `/respond`, `/change` | Answers a paid-search estimate |
| `/tools/ads/r/<token>`, `.pdf` | Monthly Google Ads report |
| `/reports/r/c/<token>`, `.pdf`, `/data.json` | Live cross-platform performance dashboard |
| `/scans/r/<token>`, `.pdf` | Site audit report |
| `/tools/ads-grader/r/<token>` | Their own grader score |
| `/tools/commercial-builder/review/<token>`, `/decide`, `/comment` | Approves a cut, leaves timecoded notes |
| `/tools/commercial-builder/review/voice/<token>`, `/submit` | Records or uploads a voice sample |
| `/tools/image-creator/review/<token>`, `/decide`, `/comment` | Approves a graphic |
| `/tools/radio-scripts/review/<token>`, `/decide`, `/comment` | Approves radio script concepts |
| `/review/<token>`, `/decide`, `/comment` | Creative Studio version review — bare, not under `/tools/` |
| `/tools/fan-radio/r/<token>`, `/api/public/<token>`, `/audio/<name>` | Hears and approves radio spots |
| `/tools/radio-promo/r/<token>`, `/api/public/`, `/file/` | The same, through `hub/radio_share.py` |
| `/tools/social/c/<token>/…` | Sends a photo, swipes ideas, approves a post, says what to write about |
| `/tools/smartforecast/embed/<token>`, `/api/public/embed/<token>` | The forecast widget on their own site; sends CORS headers |
| `/wx/<token>…` | The weather-trigger setup wizard, and its writes |
| `/hot/<token>…`, `/hot/ecwid-hook/<token>` | Their store hotsheet; the hook is Ecwid's order webhook |
| `/tools/display-ads/client-proof/<uuid>`, `/decision`, `/download` | Approves or returns a set of banners |

## Published by slug, living on somebody else's website

| Link | Notes |
|---|---|
| `/sales/landing/p/<slug>`, `/opened` | A built landing page, often pasted onto the client's own domain |
| `/scans/w/<slug>`, `/embed/<slug>`, `/api/w/<slug>/check\|audit\|unlock` | The AI-visibility widget; `unlock` files a lead |
| `/tools/calculators/c/<slug>`, `/embed/<slug>`, `/api/<slug>/estimate\|unlock`, `/embed.js` | Media calculators |
| `/industry/p/<id>`, `/report`, `/industry/widget/<id>`, `/embed`, `/embed.js` | Published industry pages and the opportunity widget |
| `/llms/<slug>/llms.txt` | A client's published llms.txt, reached by a 301 from their own domain |

## Open to anyone, with no token at all

`/land/*` — nine industry landing pages and their lead capture.
`/msa/*` — the MSA signing page and its PDF.
`/tools/ads-grader/*` and `/tools/marketing-audit/*` — whole tools, prefix
exemptions rather than route lists, because neither has a staff screen anywhere
in it.
`/suite-app`, `/suite-app/start` — the Suite SSO frame and its getting-started
tab.
`/login`, `/login/health`, `/signup`, `/forgot`, `/health`, `/healthz`,
`/api/version`, `/robots.txt`, `/llms.txt`, and the shared front-end assets.

## The rules that hold across all of them

**An unknown token answers the same 404 as a revoked one.** A page that says
"this link has expired" tells somebody probing which tokens are real.

**Read and write are separate allowlists.** A page somebody may look at is not a
form they may submit, and folding the two together lets an entry written for a
readable page quietly cover a route that creates something.

**A client-facing share token is `secrets.token_urlsafe(24)` or `(32)`** —
measured across scans, reports, 360 Skills, SmartForecast, LSA, Image Picker,
Image Creator, Creative Studio, Radio Scripts, Smart 1 Ads, the calculators and
`hub/radio_share.py`. Narrower calls exist in the same modules and are *not*
links: `token_urlsafe(9)` is a track id, `(12)` a plan id, `(6)` and `(8)` parts
of an asset filename. Do not read a short one as a weak link without checking
what it addresses, and do not copy one for a link.

**A signed-in session is refused at `/sales/builder/api/p/<token>/accept`** — a
rep cannot accept a proposal on the client's behalf. That is the opposite gate
from the one the sweep looks for, and worth knowing exists.

## Two blind spots

**Proxied Node modules.** The Display Ad Builder and the Marketing Efficiency
Audit run as separate processes and are reached through
`hub/ad_builder_proxy.py` and `hub/marketing_audit_proxy.py`. Their internal
routes are not Flask rules, so the route sweep sees one `<path:path>` rule and
nothing under it. The ad builder keeps its own anchored `PUBLIC_PATTERNS`;
`api/proof/<id>/rebuild` is deliberately *not* on it, because rebuilding
re-renders for everyone holding the link and reaches endpoints billed per call.
The audit tool is public whole. **Adding a route inside either Node app does not
pass this repo's checks at all.**

**Cloudinary delivery URLs.** `hub/storage.py` uploads with `type="upload"`,
which is public delivery. Every render, image and audio file filed through it
has a permanent `res.cloudinary.com/…/upload/…` URL reachable with no Hub
session **and no token**. That is by design — the standing directive in
CLAUDE.md says to preserve full delivery URLs and permissions — but it is the
one category where revoking a token changes nothing, because there was never a
token. Anything genuinely sensitive must not be filed as a public Cloudinary
object in the first place.

## Adding a client-facing link

Three things, and two of them are easy to forget because each half looks
complete on its own:

1. **The login exemption.** `PUBLIC_PREFIXES` on a mounted module, or
   `blueprint_guard.install(bp, public=…)` on a blueprint.
2. **The chrome exemption.** An entry in `CHROMELESS` in `hub/__init__.py`. A
   mounted module gets both from `_mount()`; a blueprint gets neither
   automatically and needs both written out.
3. **An entry in the right allowlist in `test_blueprint_guards.py`**, with the
   reason. The sweep fails otherwise, which is the intent: a new public route is
   a decision somebody writes down, not something that lands quietly.

Exempting a path from the login and not from the chrome hands a client the staff
sidebar. The other way round hands them a sign-in form for an account they will
never have. `test_blueprint_guards.py` holds both halves.
