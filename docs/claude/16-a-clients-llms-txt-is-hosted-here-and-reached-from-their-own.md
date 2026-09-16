## A client's llms.txt is hosted here and reached from their own domain

`hub/llms_txt.py` builds one; `hub/llms_hosting.py` is everything after that.
The file is served at `/llms/<slug>/llms.txt` on this host and reached by a
**301** from `<clientdomain>/llms.txt`, which is a redirect rule in the
client's own site builder. No DNS change, no CDN, and no dependency on Smart 1
Sites ever supporting root-file uploads.

**One dedicated prefix, not a slug at the root.** `smart1.agency/<slug>/llms.txt`
was the obvious shape and is one path to allow in robots.txt, one to exempt
from the crawler header, one entry in the guard allowlist — and no chance of a
client slug shadowing a Hub route. A client called "status" or "activity" at
the root would have taken a staff page down, and the only sign would have been
a 404 nobody could explain.

**The brief named one layer that had to be opened and there were three.** Each
of the other two would have defeated the whole feature silently, with every
screen in the Hub reporting a clean publish while a crawler read nothing:

* **robots.txt.** `smart1.agency` refuses everything, correctly, and both the
  crawlers this is for honour it. `no_crawl.LLMS_READERS` is the eight agents
  that may read the prefix, and each gets `Allow:` **inside its own group**:
  robots.txt is matched by **user-agent group, never by substring**, so a
  group naming GPTBot says nothing whatever about ClaudeBot — and a bare
  `Allow:` under `User-agent: *` is not a fix either, since the original
  standard has no `Allow` directive and parser behaviour still varies.
  Deliberately a subset of `AI_CRAWLERS`: `Google-Extended` and
  `Applebot-Extended` are AI-**training** opt-outs and stay refused, because
  these files are for retrieval at answer time and nothing on this host is for
  a training set.
* **`X-Robots-Tag`.** `no_crawl.NoIndex` stamps `noindex, nofollow, …, noai,
  noimageai` onto *every* response in the composed app. `noai` on a file whose
  entire purpose is to be read by AI is the flattest contradiction available,
  and it is in no template and no route — it is added by middleware three
  layers out in `wsgi.py`, so it is invisible from the route that serves the
  file. That middleware already declined to overwrite a header a response set
  for itself, and its docstring said no route in the Hub had a reason to.
  One does now, and it says `noindex` alone: keep a raw text file out of
  search results, say nothing that tells its actual readers to leave it alone.
  **The 301 needs it too** — left to the default, a crawler that honours
  `noai` may not follow the redirect, closing the migration path for exactly
  the clients still on the old address. Found by requesting the route rather
  than by reading it.
* **The chrome.** `text/plain`, so the injector skips it on the mimetype —
  and `/llms/` is in `CHROMELESS` anyway, so it is a decision rather than a
  coincidence of that check.

**The slug is stored, which is the opposite of the rule everywhere else.**
`hub/client_key.py` refuses to store a derived key so a client renamed in
Knack re-joins on the next request. Here the same reasoning inverts: the slug
is written into a redirect rule **on somebody else's website**, so it has to
outlive the rename. Derived, a rename would 404 every request the client's own
site sends us, in silence. The registry is also the only place uniqueness can
be enforced — two businesses whose names slugify alike would otherwise share
one address, which is a third party's redirect quietly pointing at another
client's file — and it makes the public route a dict lookup rather than a walk
of the whole client book on every crawler request. Unpublishing **keeps** the
slug: a rule on their site still points here, and re-issuing a different
address later leaves it aimed at a 404 nobody is watching.

**Publishing is a separate act from saving, and saving used to undo it.**
`llms_txt.save()` assigned a fresh `{"text", "updated"}` over the whole record
— destroying the `published` copy beside it, so saving a draft took the live
file down and the next read adopted the half-written draft in its place, with
the screen reporting a clean save either way. The Commercial Builder's
`set_music` trap, one module over. **Every write to that record merges now.**

**The one migration is written down rather than assumed.** A record saved
before publishing existed has been served publicly all along, so refusing to
serve it now would take a live file off the air to satisfy a rule introduced
afterwards. Such a draft is adopted as published, once, and says
`from_draft` so the screen can tell it from a deliberate publish. Safe rather
than lenient: `save()` has always refused text containing a `NEED`
placeholder, which is the same gate publishing applies.

**The verifier is the point of the whole build.** The redirect is on a site we
do not control, the file is on a host whose crawler policy other work here
edits, and every failure is silent. `verify()` follows the chain **one hop at
a time** — `allow_redirects=True` collapses it into a final answer and throws
away the status codes, which is the entire question, since 301 and 302 both
end at the same 200 and only one of them is stored. It records the hop count,
the final status, content type, bytes and sha against the published copy, TLS
per hop, and robots at **both** ends per agent.

Three verdicts and a fourth state. **Pass** is one hop, a 301, a 200,
`text/plain`, bytes matching, robots allowing all three. **Warn** is reachable
and losing reach: a 302 (a crawler treats it as temporary), extra hops, a
landing host that is neither theirs nor ours — which is what "still on the
retired S3 bucket" looks like — or content that has drifted from what we
published. **Fail** is not reachable as a text file at all: a non-200, an HTML
content type, robots refusing at either end, or **a redirect landing on the
sign-in page**, which is the quietest of them: 200, a body, and a crawler
recording our login form as the client's llms.txt. And a robots.txt that 404s
means nothing is restricted, which is a real answer, while one we could not
**reach** is `measured: False` — reading a network failure as permission is a
green tick over a question nobody asked.

**One divergence from the written brief, stated rather than buried.** The
brief lists "final host is not the client's domain" as a Warn. Under this
design the final host is never the client's domain — that is what hosting in
the Hub means — so applied literally, Pass is unreachable and the column is a
wall of amber nobody reads. The actionable question is whether it landed
somewhere **unexpected**; the off-domain caveat is carried as a note on every
result instead, because it is a property of the architecture rather than a
finding about any one client.

**And the claim is kept honest in one place.** `CAVEAT` is the sentence, read
by the screen rather than restated in it: no major provider has confirmed it
reads llms.txt at inference time, a bot requesting the file proves access
rather than influence, and Google has said it does not use the file. It is a
low-cost, well-executed deliverable and must not be sold as a ranking lever.
`RUNBOOK` is the five Simvoly steps, likewise data, so the screen and the test
read one list.

**A nightly job asks, because nothing else will.** `llms_verify` re-checks
only the **published** clients — one with nothing live has nothing that can
have broken — writes each result against the client, and logs a row only when
something is failing: a clean sweep is a *state*, and writing one every night
for ever is the noise `hub/google_index.py` had to learn to stop making.

**`test_blueprint_guards.py` could not see this route, and the note saying
so was read for a year as a limitation rather than as the gap it was.** Its
sweep probed every route **with no variable in it**, so `/llms/<slug>/llms.txt`
was never requested — and neither was anything else with a `<` in it, which is
**330 of the 1048 routes the composed app serves**. That third is not a
remainder: every client-facing surface in this Hub is addressed by a token or
a slug, so "which parameterized routes answer a stranger" is very nearly the
question that file exists to ask, and it was the half nobody had asked.

It is swept now, with an inert value nothing in the book matches. **A 404 is
reached, not refused** — a guard runs in `before_request`, ahead of the view,
so it redirects whether or not the token resolves; a route answering 404 has
nothing in front of it. That is what makes an unresolvable id a fair probe
rather than a way of dodging the question, and it is why nearly every answer
in that section is a 404 and the reading still holds.

**It went in green**, which is the only way it was worth adding: all 63
reachable routes are the token- and slug-addressed client surfaces the design
intends — the scan widget and its report, the proposal a client accepts, the
paid-search estimate, the commercial review link, the radio approval page, the
client's four social pages, the upload picker, the calculators, the MSA PDF,
the Google Access consent flow and the static assets each of those loads. Each
is named with the reason it is public, in **two more dicts** rather than an
extension of the existing pair: an entry written for a fixed page must not
quietly cover a parameterized route added under the same prefix later, which
is the same reason reads and writes were split in the first place.

**Two things it had to get right, and the second nearly repeated the failure
this file was rewritten once to close.** A probe value has to *build*: an
integer converter refuses a word and a uuid converter refuses both, so a first
pass that picked the value from the converter's class name — and got that name
wrong for integers — silently dropped **76 rules**, printed a healthy-looking
count, and swept 254 rules while claiming 330. Values are tried in turn now
and anything that would not build at all is **named rather than passed over**.
And the two client-facing halves are asserted from opposite ends: four
surfaces are checked **by name** to still open for a client, because a
parameterized route falling *behind* the login is a sign-in form in front of
somebody who will never have an account — the failure Fan Radio shipped with
for as long as it took anybody to send the link.

The direct assertion in `test_llms_hosting.py` stays — anonymous, through the
composed app, headers included — because that one is about the crawler headers
as well as the openness.
