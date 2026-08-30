"""hub/llms_hosting.py — hosting, the redirect, and the check that says so.

    python3 test_llms_hosting.py

Same shape as the other test files: no pytest, no new dependencies, and it
runs against a temporary data directory and a throwaway SQLite database, so it
never touches /var/data or the real one. Every outbound request is stubbed —
what is worth asserting is what the verifier concludes when somebody else's
server answers badly, and that cannot be arranged against a live host.

## What this file is holding up

The feature is a redirect on a website we do not control, pointing at a file
on a host whose crawler policy other work here edits. Every part of it can be
broken by somebody who never heard of it, and **every one of those failures is
silent**: the Hub goes on reporting a clean publish while a crawler reads a
404, a login page, or a refusal.

  1.  the path is open, per agent      — robots.txt is matched by user-agent
                                         GROUP, never by substring, so a group
                                         naming GPTBot says nothing about
                                         ClaudeBot
  2.  and only that path               — a prefix wide enough to fix this goes
                                         wrong in the other direction just as
                                         quietly
  3.  the header does not say `noai`   — the NoIndex middleware stamps it onto
                                         every response in the composed app,
                                         and `noai` on a file published for AI
                                         to read is the flattest contradiction
                                         available
  4.  the route is outside the login   — a crawler served the sign-in page
                                         records that as the client's file
  5.  and outside the chrome           — plain text, so the injector skips it
  6.  the published copy, never the    — publishing is a separate act so that a
      draft                              half-written file is never live
  7.  the slug is stored and stable    — it is inside a redirect rule on
                                         somebody else's website
  8.  the old address 301s             — that URL is already in those rules
  9.  revoked == never existed         — a 404 that distinguishes them tells
                                         whoever is probing which slugs are real
  10. 302 is a Warn, 301 is not        — the finding this was built for
  11. a login page is a Fail           — the quietest way this breaks
  12. drift is a Warn                  — served text that is not what we
                                         published
  13. cannot look != nothing wrong     — a robots.txt we could not reach is
                                         not measured, never "allowed"
  14. the two writes are reported      — storing the file and recording its
      apart                              address can succeed separately
  15. the sweep only checks the live   — and a clean sweep writes no log row
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="s1llms_test_")
os.environ["HUB_DATA_DIR"] = os.path.join(TMP, "data")
os.environ["AUDIT_LOG_PATH"] = os.path.join(TMP, "audit.jsonl")
# Both, deliberately. A fresh HUB_DATA_DIR in front of an *inherited*
# DATABASE_URL gives an empty disk in front of a full jsonstore mirror, and
# the second run reads the first one's writes — the trap CLAUDE.md names.
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(TMP, "db.sqlite3")
os.environ.setdefault("PUBLIC_BASE_URL", "https://smart1.agency")

import urllib.robotparser                                # noqa: E402

from hub import llms_hosting as lh                       # noqa: E402
from hub import no_crawl                                 # noqa: E402

_passed, _failed = 0, 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}\n          got:  {got!r}\n          want: {want!r}")


def section(title):
    print(f"\n{title}")


# ---------------------------------------------------------------------------
section("1-2. robots.txt opens the one prefix, per user-agent group")
# ---------------------------------------------------------------------------
TXT = no_crawl.robots_txt()
P = urllib.robotparser.RobotFileParser()
P.parse(TXT.splitlines())

LLMS = "https://smart1.agency/llms/schmidts/llms.txt"
ELSE = "https://smart1.agency/client360"

for agent in ("GPTBot", "ChatGPT-User", "OAI-SearchBot",
              "ClaudeBot", "Claude-User", "Claude-SearchBot",
              "PerplexityBot", "Perplexity-User"):
    check(f"{agent} may read a client file", P.can_fetch(agent, LLMS), True)
    check(f"{agent} may read nothing else", P.can_fetch(agent, ELSE), False)

# The other direction. A prefix wide enough to fix this goes wrong the other
# way just as quietly, and Google-Extended / Applebot-Extended are AI-TRAINING
# opt-outs: the client files are for retrieval at answer time, and nothing on
# this host is for a training set.
for agent in ("Google-Extended", "Applebot-Extended", "Googlebot", "Bingbot",
              "CCBot", "Bytespider", "SomeCrawlerNobodyHasHeardOf"):
    check(f"{agent} is refused the client files too",
          P.can_fetch(agent, LLMS), False)

check("the wildcard group still refuses everything",
      P.can_fetch("*", LLMS) or P.can_fetch("*", ELSE), False)
# Case and the Name/version form: a crawler sends "GPTBot/1.2", not "GPTBot".
check("the group matches the version form crawlers actually send",
      P.can_fetch("GPTBot/1.2", LLMS), True)

# Every reader named has a group of its own in the file. A reader in
# LLMS_READERS that is not in AI_CRAWLERS would be permitted by a group that is
# never written, which reads exactly like a working allow.
missing = [a for a in no_crawl.LLMS_READERS if a not in no_crawl.AI_CRAWLERS]
check("every named reader actually gets a group in the file", missing, [])
check("and the prefix is the module's, not a second spelling",
      lh.PUBLIC_PREFIX in TXT, True)


# ---------------------------------------------------------------------------
section("3-5. the response a crawler actually receives")
# ---------------------------------------------------------------------------
import wsgi                                              # noqa: E402
from werkzeug.test import Client                         # noqa: E402

anon = Client(wsgi.application)

CLIENT = "Schmidts Sausage Haus"
BODY = ("# Schmidts Sausage Haus\n\n> A German restaurant in Columbus, Ohio.\n\n"
        "**Phone:** 614 555 0100\n")

lh.publish(CLIENT, BODY, actor="test")
SLUG = lh.slug_for(CLIENT)
check("a slug is allocated on publish", SLUG, "schmidts-sausage-haus")

r = anon.get(f"/llms/{SLUG}/llms.txt")
check("a crawler with no session gets the file", r.status_code, 200)
check("as plain text", r.headers.get("Content-Type", ""),
      "text/plain; charset=utf-8")
check("with the published bytes", r.get_data(as_text=True), BODY)

tag = r.headers.get("X-Robots-Tag", "")
# The middleware default carries `noai, noimageai`. On this file that is the
# feature refusing its own readers, and nothing on any screen would say so.
check("the header does not tell AI to leave it alone", "noai" in tag, False)
check("nor suppress the snippet", "nosnippet" in tag, False)
check("but it is still kept out of search results", tag, "noindex")
check("and cached briefly", r.headers.get("Cache-Control", ""),
      "public, max-age=300")

body = r.get_data()
check("no sidebar is injected into it", b"s1hub-sb" in body, False)
check("nor the help layer", b"hub-help.js" in body, False)
check("the prefix is named in CHROMELESS rather than left to the mimetype",
      '"/llms/"' in Path("hub/__init__.py").read_text(), True)


# ---------------------------------------------------------------------------
section("6. the published copy, never the draft")
# ---------------------------------------------------------------------------
from hub import llms_txt as builder                      # noqa: E402

builder.save(CLIENT, BODY + "\n## An edit nobody has published\n")
r = anon.get(f"/llms/{SLUG}/llms.txt")
check("a saved draft does not go live by itself", r.get_data(as_text=True), BODY)

lh.publish(CLIENT, BODY + "\n## Now it is published\n", actor="test")
r = anon.get(f"/llms/{SLUG}/llms.txt")
check("publishing is what changes what is served",
      "Now it is published" in r.get_data(as_text=True), True)

bad = lh.publish(CLIENT, "# X\n\n- NEED ANSWER - what they do not do\n")
check("a file with a NEED placeholder is refused", bad.get("ok"), False)
r = anon.get(f"/llms/{SLUG}/llms.txt")
check("and the refusal did not disturb what is live",
      "Now it is published" in r.get_data(as_text=True), True)

# The one migration. A record saved before publishing existed has been served
# publicly all along; refusing to serve it now would take a live file off the
# air to satisfy a rule introduced afterwards.
OLD = "Legacy Client"
builder.save(OLD, "# Legacy Client\n\n> Saved before publish existed.\n")
adopted = lh.published(OLD)
check("a pre-existing saved draft is adopted as published",
      bool(adopted.get("body")), True)
check("and says so, rather than reading as a deliberate publish",
      adopted.get("from_draft"), True)


# ---------------------------------------------------------------------------
section("7-9. the address: stable, redirected, and unrevealing")
# ---------------------------------------------------------------------------
check("the slug survives being asked for again", lh.slug_for(CLIENT), SLUG)
check("and resolves back to the client", lh.client_for_slug(SLUG), CLIENT)
check("the public URL is built from PUBLIC_BASE_URL",
      lh.public_url(CLIENT), f"https://smart1.agency/llms/{SLUG}/llms.txt")

# Two clients whose names slugify the same must not collide: the second one
# would otherwise take over the first's URL, which is a redirect rule on a
# third party's website silently pointing at another business's file.
a = lh.slug_for("Acme Roofing")
b = lh.slug_for("Acme  Roofing!")
check("a colliding name gets its own slug rather than the other's", a == b, False)
check("and the first keeps the address already handed out", a, "acme-roofing")

# PUBLIC_BASE_URL is an origin, and one env group on this deployment holds a
# whole callback URL in it. A path in it would land inside every redirect rule.
check("a path in PUBLIC_BASE_URL is trimmed rather than pasted through",
      lh.public_url(CLIENT, "https://x.test/tools/ads/oauth/callback"),
      f"https://x.test/llms/{SLUG}/llms.txt")

r = anon.get(f"/llms/{SLUG}.txt")
check("the address this used to be served at still answers", r.status_code, 301)
check("as a permanent redirect a crawler stores",
      r.headers.get("Location", "").endswith(f"/llms/{SLUG}/llms.txt"), True)
# The redirect carries the header too. Left to the middleware default it goes
# out with `noai` on it, so a crawler that honours that may not follow it --
# closing the migration path for exactly the clients still on the old address.
check("and the redirect itself does not tell AI to ignore it",
      r.headers.get("X-Robots-Tag", ""), "noindex")

for path in (f"/llms/{SLUG}-not-a-real-one/llms.txt",
             "/llms/never-existed/llms.txt",
             "/llms/never-existed.txt"):
    check(f"{path} answers a bare 404",
          anon.get(path).status_code, 404)

lh.unpublish(CLIENT, actor="test")
check("a file taken off the air answers the identical 404",
      anon.get(f"/llms/{SLUG}/llms.txt").status_code, 404)
check("and the address is kept, so republishing re-uses it",
      lh.client_for_slug(SLUG), CLIENT)
lh.publish(CLIENT, BODY, actor="test")


# ---------------------------------------------------------------------------
section("10-13. the verifier: what it concludes when a server answers badly")
# ---------------------------------------------------------------------------
import hashlib                                           # noqa: E402

PUB_SHA = hashlib.sha256(BODY.encode()).hexdigest()

ROBOTS_OPEN = ""                       # a 404 robots.txt: nothing restricted
ROBOTS_SHUT = "User-agent: *\nDisallow: /\n"


class Fake:
    """A stubbed internet: a dict of URL -> answer, and nothing else.

    Deliberately a table rather than a live host. What is worth asserting is
    what the verifier *concludes* when somebody else's server answers badly,
    and a 302 that nobody controls cannot be arranged on demand.
    """

    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def __call__(self, url, method="GET"):
        self.asked.append(url)
        a = self.answers.get(url)
        if a is None:
            return {"url": url, "status": 404, "headers": {}, "body": b"",
                    "error": "", "tls_ok": True, "truncated": False}
        if isinstance(a, str):                      # an error
            return {"url": url, "status": None, "headers": {}, "body": b"",
                    "error": a, "tls_ok": True, "truncated": False}
        status, headers, body = a
        return {"url": url, "status": status,
                "headers": {k.lower(): v for k, v in headers.items()},
                "body": body if isinstance(body, bytes) else body.encode(),
                "error": "", "tls_ok": True, "truncated": False}


def run(answers, client=CLIENT, domain="schmidts.test"):
    real_fetch, real_domain = lh._fetch, lh.client_domain
    lh._fetch = Fake(answers)
    lh.client_domain = lambda c: domain
    try:
        return lh.verify(client)
    finally:
        lh._fetch, lh.client_domain = real_fetch, real_domain


PUBLIC = f"https://smart1.agency/llms/{SLUG}/llms.txt"
TEXT = {"Content-Type": "text/plain; charset=utf-8"}

# --- the arrangement the runbook asks for -------------------------------
good = {
    "https://schmidts.test/llms.txt": (301, {"Location": PUBLIC}, ""),
    PUBLIC: (200, TEXT, BODY),
    "https://schmidts.test/robots.txt": (200, {}, ROBOTS_OPEN),
    "https://smart1.agency/robots.txt": (200, {}, TXT),
}
res = run(good)
check("a 301 to a 200 text/plain that matches is a Pass", res["verdict"], "pass")
check("and it is measured", res["measured"], True)
check("one hop", res["hop_count"], 1)
check("the sha is compared, not assumed", res["sha256"], PUB_SHA)
check("the off-domain caveat rides as a note rather than a finding",
      any("rather than the client" in n for n in res["notes"]), True)
check("and the standing caveat is on every result",
      res["caveat"], lh.CAVEAT)

# --- 302: the finding this whole build exists for -----------------------
res = run({**good, "https://schmidts.test/llms.txt":
           (302, {"Location": PUBLIC}, "")})
check("a 302 is a Warn, not a Pass", res["verdict"], "warn")
check("and it says to set the rule to 301",
      any("301" in w for w in res["warns"]), True)
check("a 302 is never reported as broken", res["fails"], [])

# --- more than one hop ---------------------------------------------------
res = run({**good,
           "https://schmidts.test/llms.txt":
               (301, {"Location": "https://files.smart1marketing.test/x"}, ""),
           "https://files.smart1marketing.test/x": (301, {"Location": PUBLIC}, ""),
           })
check("two hops is a Warn", res["verdict"], "warn")
check("and the count is named", res["hop_count"], 2)

# --- landed somewhere that is neither theirs nor ours --------------------
OLDS3 = "https://files.smart1marketing.test/llms/schmidts/llms.txt"
res = run({"https://schmidts.test/llms.txt": (301, {"Location": OLDS3}, ""),
           OLDS3: (200, TEXT, BODY),
           "https://schmidts.test/robots.txt": (200, {}, ROBOTS_OPEN),
           "https://files.smart1marketing.test/robots.txt": (404, {}, ""),
           })
check("still pointing at the retired host is a Warn", res["verdict"], "warn")
check("named as not-yet-repointed rather than as an error",
      any("repointed" in w for w in res["warns"]), True)

# --- served at the client's own root -------------------------------------
res = run({"https://schmidts.test/llms.txt": (200, TEXT, BODY),
           "https://schmidts.test/robots.txt": (200, {}, ROBOTS_OPEN)})
check("a true root serve with no redirect at all is a Pass",
      res["verdict"], "pass")
check("and is called the strongest arrangement rather than being silent",
      any("no redirect at all" in n for n in res["notes"]), True)

# --- the quietest failure there is ---------------------------------------
LOGIN = "https://smart1.agency/login?next=/llms/x"
res = run({"https://schmidts.test/llms.txt": (302, {"Location": LOGIN}, ""),
           LOGIN: (200, {"Content-Type": "text/html"},
                   "<html><form><input type=\"password\"></form></html>"),
           "https://schmidts.test/robots.txt": (200, {}, ROBOTS_OPEN),
           "https://smart1.agency/robots.txt": (200, {}, TXT)})
check("a redirect that lands on a sign-in page is a Fail",
      res["verdict"], "fail")
check("and says what a crawler would have recorded",
      any("sign-in page" in f for f in res["fails"]), True)

# --- HTML where a text file should be ------------------------------------
res = run({**good, PUBLIC: (200, {"Content-Type": "text/html"},
                            "<html><body>Not found</body></html>")})
check("an HTML content type is a Fail", res["verdict"], "fail")

# --- non-200 --------------------------------------------------------------
res = run({**good, PUBLIC: (404, TEXT, "Not found.\n")})
check("a 404 at the end of the chain is a Fail", res["verdict"], "fail")

# --- drift ----------------------------------------------------------------
res = run({**good, PUBLIC: (200, TEXT, BODY + "\nEdited at the other end.\n")})
check("content that is not what we published is a Warn", res["verdict"], "warn")
check("and says which of the two explanations to look at",
      any("republished" in w for w in res["warns"]), True)

# --- robots refusing, at each end separately ------------------------------
res = run({**good, "https://smart1.agency/robots.txt": (200, {}, ROBOTS_SHUT)})
check("robots refusing on the host serving the file is a Fail",
      res["verdict"], "fail")
check("and names the agents rather than saying 'blocked'",
      any("GPTBot" in f for f in res["fails"]), True)

res = run({**good, "https://schmidts.test/robots.txt": (200, {}, ROBOTS_SHUT)})
check("robots refusing on the client's own site is a Fail too",
      res["verdict"], "fail")

# A group naming one agent says nothing about another. This is the failure
# CLAUDE.md records from the scans work, arriving one feature later.
ONLY_GPT = "User-agent: GPTBot\nAllow: /llms/\nDisallow: /\n\nUser-agent: *\nDisallow: /\n"
res = run({**good, "https://smart1.agency/robots.txt": (200, {}, ONLY_GPT)})
check("a group naming GPTBot alone does not cover ClaudeBot",
      sorted(res["robots_final"]["blocked"]), ["ClaudeBot", "PerplexityBot"])

# --- could not look != nothing wrong --------------------------------------
res = run({**good,
           "https://smart1.agency/robots.txt": "ConnectionError: refused"})
check("a robots.txt we could not reach is not read as permission",
      res["verdict"], "pass")           # not a Fail: nothing was refused
check("and it says so rather than drawing a clean tick",
      any("not measured" in n for n in res["notes"]), True)
check("the reason travels with it",
      any("ConnectionError" in n for n in res["notes"]), True)

res = run({"https://schmidts.test/llms.txt": "ConnectionError: no route"})
check("a site that cannot be reached at all is not measured as fine",
      res["verdict"], "fail")
check("and the exception type travels rather than 'could not reach'",
      any("ConnectionError" in r for r in res["reasons"]), True)

# --- no website on file ----------------------------------------------------
res = run(good, domain="")
check("a client with no website is not measured, never a Fail",
      res["verdict"], "not_measured")
check("and names the field that would fix it",
      any("website on file" in n for n in res["notes"]), True)

# --- a loop ---------------------------------------------------------------
res = run({"https://schmidts.test/llms.txt":
           (301, {"Location": "https://schmidts.test/llms.txt"}, "")})
check("a redirect loop is caught rather than followed", res["verdict"], "fail")


# ---------------------------------------------------------------------------
section("registrable domain: a stated heuristic, not a guess")
# ---------------------------------------------------------------------------
for host, want in (("www.schmidts.com", "schmidts.com"),
                   ("shop.schmidts.com", "schmidts.com"),
                   ("schmidts.com", "schmidts.com"),
                   ("files.smart1marketing.com", "smart1marketing.com"),
                   ("a.b.example.co.uk", "example.co.uk"),
                   ("", "")):
    check(f"{host or '(blank)'} -> {want or '(blank)'}",
          lh.registrable(host), want)


# ---------------------------------------------------------------------------
section("14-15. the two writes, and the sweep")
# ---------------------------------------------------------------------------
out = lh.publish("Another Client", "# Another Client\n\n> A business.\n")
check("publishing reports storing the file", out.get("published"), True)
check("and recording its address, separately", out.get("slug_recorded"), True)
check("so a screen can say 'stored, but not reachable' when it has to",
      "slug_error" in out, True)

from hub import audit                                     # noqa: E402
rows = audit.read(limit=50, module="seo")
check("publishing is attributable on the client's own record",
      any(r.get("type") == "llms_txt_published" for r in rows), True)
check("under a module the work log can name",
      all(r.get("module") == "seo" for r in rows
          if r.get("type") == "llms_txt_published"), True)

# The sweep only walks what is live: a client with no published file has
# nothing that can have broken, and asking anyway spends four outbound
# requests each on the whole book to learn nothing.
seen = []
real_verify = lh.verify
lh.verify = lambda c, base="": (seen.append(c) or
                                {"verdict": "pass", "measured": True})
try:
    swept = lh.sweep(actor="test")
finally:
    lh.verify = real_verify
check("the sweep checks the published clients", sorted(seen),
      sorted([CLIENT, "Another Client"]))
check("and not the ones with nothing live", "Acme Roofing" in seen, False)
check("it reports how many it checked", swept["checked"], len(seen))
check("a clean sweep writes no activity-log row",
      any(r.get("type") == "llms_txt_verify_problems"
          for r in audit.read(limit=50, module="seo")), False)

lh.verify = lambda c, base="": {"verdict": "fail", "measured": True,
                                "reasons": ["robots.txt refuses GPTBot."]}
try:
    swept = lh.sweep(actor="test")
finally:
    lh.verify = real_verify
check("a failing sweep does write one",
      any(r.get("type") == "llms_txt_verify_problems"
          for r in audit.read(limit=50, module="seo")), True)
check("naming the clients rather than a count alone",
      sorted(p["client"] for p in swept["problems"]),
      sorted([CLIENT, "Another Client"]))


# ---------------------------------------------------------------------------
section("the scheduler runs it, and the screen renders the caveat")
# ---------------------------------------------------------------------------
from hub import scheduler                                 # noqa: E402
check("the nightly check is a registered job",
      "llms_verify" in scheduler.JOBS, True)
check("at twice a day rather than hourly",
      scheduler.JOBS["llms_verify"][0], 720)

page = Path("hub/templates/seo_client.html").read_text()
check("the client screen draws the verdict", "llmsVerdict" in page, True)
check("offers the check", "verifyLlms" in page, True)
check("publishes deliberately, apart from saving",
      "publishLive" in page and "Save draft" in page, True)
check("and renders the caveat rather than restating it",
      "d.caveat" in page, True)
check("the runbook comes from the server, not a second copy in the page",
      "d.runbook" in page and len(lh.RUNBOOK) == 5, True)
check("the URL is never slugified in the browser",
      "llms/'+slug" in page or "replace(/[^a-z0-9]+/g" in page, False)


# ------------------------------------------------------------------- summary
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'-' * 60}\n{_passed} passed, {_failed} failed")
sys.exit(1 if _failed else 0)
